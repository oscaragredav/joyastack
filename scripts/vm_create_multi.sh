#!/bin/bash
# ------------------------------------------------------------
# Script: vm_create_multi.sh
# Descripción: Crea una VM con múltiples interfaces TAP en diferentes VLANs
# Parámetros:
#  $1 = NombreVM
#  $2 = NombreOvS
#  $3 = VLAN_IDS (separados por comas, ej: "100,200,300")
#  $4 = PuertoVNC
#  $5 = CPUs
#  $6 = RAM MB
#  $7 = Disco GB
#  $8 = NUM_IFACES
#  $9 = IMAGE_PATH
# ------------------------------------------------------------

set -euo pipefail

log() { echo "[$(date +'%H:%M:%S')] $*"; }
error_exit() { log "ERROR: $*"; exit 1; }

if [ $# -ne 9 ]; then
  error_exit "Uso: $0 <NombreVM> <OvS> <VLAN_IDS> <VNC_PORT> <CPUs> <RAM_MB> <DISK_GB> <NUM_IFACES> <IMAGE_PATH>"
fi

VM_NAME=$1
OVS_NAME=$2
VLAN_IDS=$3
VNC_PORT=$4
CPUS=$5
RAM=$6
DISK=$7
NUM_IFACES=$8
IMAGE_PATH=$9

VM_IMG="/home/ubuntu/joyastack/var/vms/${VM_NAME}.qcow2"

log "=== Creando VM: $VM_NAME con VLANs: $VLAN_IDS ==="

# --- Validar prerequisitos ---
ovs-vsctl br-exists "$OVS_NAME" || error_exit "El bridge $OVS_NAME no existe."
[ -f "$IMAGE_PATH" ] || error_exit "No se encontró la imagen: $IMAGE_PATH"

# --- Limpiar procesos previos ---
existing_pid=$(pgrep -f "qemu-system-x86_64.*-name $VM_NAME" 2>/dev/null || true)
[ -n "$existing_pid" ] && kill -9 "$existing_pid" 2>/dev/null || true

# --- Crear imagen overlay ---
mkdir -p "$(dirname "$VM_IMG")"
qemu-img create -f qcow2 -b "$IMAGE_PATH" -F qcow2 "$VM_IMG" "${DISK}G"

# --- Crear interfaces TAP y construir parámetros QEMU ---
IFS=',' read -ra VLAN_ARRAY <<< "$VLAN_IDS"
NETDEV_PARAMS=""
DEVICE_PARAMS=""

for idx in "${!VLAN_ARRAY[@]}"; do
  VLAN_ID="${VLAN_ARRAY[$idx]}"
  TAP_NAME="tap-${VM_NAME}-${idx}"
  MAC_ADDRESS="52:54:00:$(openssl rand -hex 3 | sed 's/\(..\)/\1:/g; s/:$//')"

  # Limpiar TAP previo
  ovs-vsctl --if-exists del-port "$OVS_NAME" "$TAP_NAME" 2>/dev/null || true
  ip link delete "$TAP_NAME" 2>/dev/null || true

  # Crear y conectar TAP
  ip tuntap add dev "$TAP_NAME" mode tap
  ip link set "$TAP_NAME" up
  ovs-vsctl add-port "$OVS_NAME" "$TAP_NAME" tag="$VLAN_ID"

  log "Interfaz $TAP_NAME creada en VLAN $VLAN_ID"

  # Construir parámetros QEMU
  NETDEV_PARAMS="$NETDEV_PARAMS -netdev tap,id=net${idx},ifname=$TAP_NAME,script=no,downscript=no"
  DEVICE_PARAMS="$DEVICE_PARAMS -device e1000,netdev=net${idx},mac=$MAC_ADDRESS"
done

# --- Comprobar KVM ---
KVM_FLAG=""
[ -e /dev/kvm ] && KVM_FLAG="-enable-kvm"

# --- Ejecutar QEMU ---
eval qemu-system-x86_64 \
  $KVM_FLAG \
  -name "$VM_NAME" \
  -m "$RAM" \
  -smp "$CPUS" \
  -drive file="$VM_IMG",if=virtio,format=qcow2 \
  $NETDEV_PARAMS \
  $DEVICE_PARAMS \
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
VNC_PORT=$VNC_PORT
MAC_ADDRESS=$MAC_ADDRESS
OVS_NAME=$OVS_NAME
RAM=$RAM
CPUS=$CPUS
DISK=$DISK
CREATED=$(date '+%Y-%m-%d %H:%M:%S')
EOF

log "VM $VM_NAME creada correctamente (PID $PID)"
exit 0