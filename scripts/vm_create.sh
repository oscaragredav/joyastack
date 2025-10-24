#!/bin/bash
# ------------------------------------------------------------
# Script: vm_create.sh
# Descripción: Crea una VM usando QEMU/KVM y la conecta a un
#              bridge Open vSwitch (br-int) con VLAN opcional.
# Uso:
#   ./vm_create.sh <NombreVM> <Bridge> <VLAN_ID> <VNC_PORT> <CPUs> <RAM_MB> <DISK_GB> <NUM_IFACES> <IMAGE_NAME>
# Ejemplo:
#   ./vm_create.sh VM1 br-int 0 1 2 512 5 1 ubuntu
# ------------------------------------------------------------

set -euo pipefail

# --- Validar parámetros ---
if [ $# -ne 9 ]; then
  echo "Uso: $0 <NombreVM> <Bridge> <VLAN_ID> <VNC_PORT> <CPUs> <RAM_MB> <DISK_GB> <NUM_IFACES> <IMAGE_NAME>"
  exit 1
fi

NAME="$1"
BRIDGE="$2"
VLAN_ID="$3"
VNC_PORT="$4"
CPUS="$5"
RAM_MB="$6"
DISK_GB="$7"
NUM_IFACES="$8"
IMAGE_NAME="$9"

# --- Variables ---
BASE_IMG="/var/lib/libvirt/images/${IMAGE_NAME}.qcow2"
VM_IMG="/var/lib/libvirt/images/${NAME}.qcow2"
TAP_IF="tap-${NAME}"
MAC_ADDR="52:54:00:$(openssl rand -hex 3 | sed 's/\(..\)/\1:/g; s/:$//')"

# --- Crear imagen overlay ---
if [ ! -f "$BASE_IMG" ]; then
  echo "Error: imagen base $BASE_IMG no encontrada."
  exit 1
fi
qemu-img create -f qcow2 -b "$BASE_IMG" "$VM_IMG" "${DISK_GB}G"

# --- Crear interfaz TAP y conectarla a OvS ---
ip tuntap add dev "$TAP_IF" mode tap
ip link set "$TAP_IF" up
ovs-vsctl --may-exist add-port "$BRIDGE" "$TAP_IF" tag="$VLAN_ID"

# --- Iniciar la VM ---
nohup qemu-system-x86_64 \
  -enable-kvm \
  -name "$NAME" \
  -m "$RAM_MB" \
  -smp "$CPUS" \
  -hda "$VM_IMG" \
  -net nic,macaddr="$MAC_ADDR" \
  -net tap,ifname="$TAP_IF",script=no,downscript=no \
  -vnc :$VNC_PORT \
  -daemonize > /dev/null 2>&1

echo "✅ VM $NAME creada en $BRIDGE (VLAN $VLAN_ID) con VNC :$VNC_PORT"
