#!/bin/bash
# ------------------------------------------------------------
# Script: vm_create.sh
# Descripción: Crea una VM usando QEMU y la conecta al OvS con VLAN ID específico
# Parámetros:
#  $1 = NombreVM
#  $2 = NombreOvS
#  $3 = VLAN_ID
#  $4 = PuertoVNC
#  $5 = CPUs
#  $6 = RAM MB
#  $7 = Disco GB
#  $8 = NUM_IFACES
# ------------------------------------------------------------

set -euo pipefail

# --- Fix CRLF ---
if grep -q $'\r' "$0" 2>/dev/null; then
  sed -i 's/\r$//' "$0"
  exec /usr/bin/env bash "$0" "$@"
fi

log() { echo "[$(date +'%H:%M:%S')] $*"; }
error_exit() { log "ERROR: $*"; exit 1; }

if [ $# -ne 8 ]; then
  error_exit "Uso: $0 <NombreVM> <OvS> <VLAN> <VNC_PORT> <CPUs> <RAM_MB> <DISK_GB> <NUM_IFACES>"
fi

VM_NAME=$1
OVS_NAME=$2
VLAN_ID=$3
VNC_PORT=$4
CPUS=$5
RAM=$6
DISK=$7
NUM_IFACES=$8

IMAGE_DIR="/home/ubuntu/images"
BASE_IMAGE="${IMAGE_DIR}/cirros-0.6.2-x86_64-disk.img"
VM_IMG="/home/ubuntu/joyastack/var/vms/${VM_NAME}.qcow2"
TAP_INTERFACE="${OVS_NAME}-${VM_NAME}-tap"
MAC_ADDRESS="52:54:00:$(openssl rand -hex 3 | sed 's/\(..\)/\1:/g; s/:$//')"

log "=== Creando VM: $VM_NAME ==="
log "Bridge: $OVS_NAME | VLAN: $VLAN_ID | CPUs: $CPUS | RAM: ${RAM}MB"

# --- Validar prerequisitos ---
ovs-vsctl br-exists "$OVS_NAME" || error_exit "El bridge $OVS_NAME no existe."
command -v qemu-system-x86_64 >/dev/null || error_exit "qemu-system-x86_64 no está instalado."
[ -f "$BASE_IMAGE" ] || error_exit "No se encontró la imagen base: $BASE_IMAGE"

# --- Limpiar procesos y TAP previos ---
existing_pid=$(pgrep -f "qemu-system-x86_64.*-name $VM_NAME" 2>/dev/null || true)
[ -n "$existing_pid" ] && kill -9 "$existing_pid" 2>/dev/null || true
ovs-vsctl --if-exists del-port "$OVS_NAME" "$TAP_INTERFACE" 2>/dev/null || true
ip link delete "$TAP_INTERFACE" 2>/dev/null || true

# --- Crear imagen overlay ---
mkdir -p "$(dirname "$VM_IMG")"
qemu-img create -f qcow2 -b "$BASE_IMAGE" "$VM_IMG" "${DISK}G"

# --- Crear y conectar interfaz TAP ---
ip tuntap add dev "$TAP_INTERFACE" mode tap
ip link set "$TAP_INTERFACE" up
ovs-vsctl add-port "$OVS_NAME" "$TAP_INTERFACE" tag="$VLAN_ID"

# --- Comprobar KVM ---
KVM_FLAG=""
[ -e /dev/kvm ] && KVM_FLAG="-enable-kvm"

# --- Ejecutar QEMU ---
qemu-system-x86_64 \
  $KVM_FLAG \
  -name "$VM_NAME" \
  -m "$RAM" \
  -smp "$CPUS" \
  -drive file="$VM_IMG",if=virtio,format=qcow2 \
  -netdev tap,id=${VM_NAME}-netdev,ifname=$TAP_INTERFACE,script=no,downscript=no \
  -device e1000,netdev=${VM_NAME}-netdev,mac=$MAC_ADDRESS \
  -vnc :$VNC_PORT \
  -daemonize

sleep 2
PID=$(pgrep -f "qemu-system-x86_64.*-name $VM_NAME" || true)
[ -z "$PID" ] && error_exit "No se pudo iniciar la VM."

# --- Guardar info ---
INFO_FILE="/home/ubuntu/joyastack/var/vms/${VM_NAME}_info.txt"
cat > "$INFO_FILE" <<EOF
VM_NAME=$VM_NAME
PID=$PID
TAP_INTERFACE=$TAP_INTERFACE
VLAN_ID=$VLAN_ID
VNC_PORT=$VNC_PORT
MAC_ADDRESS=$MAC_ADDRESS
OVS_NAME=$OVS_NAME
RAM=$RAM
CPUS=$CPUS
DISK=$DISK
CREATED=$(date '+%Y-%m-%d %H:%M:%S')
IMAGE=$BASE_IMAGE
EOF

log "VM $VM_NAME creada correctamente (PID $PID)"
log "Info: $INFO_FILE"
exit 0
