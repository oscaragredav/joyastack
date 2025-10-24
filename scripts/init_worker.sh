#!/bin/bash

# Script: init_worker.sh
# Descripción: Inicializa un Worker creando un OvS local y conectando interfaces
# Parámetros: $1=nombreOvS, $2=InterfacesAConectar (separadas por comas)

if [ $# -ne 2 ]; then
    echo "Uso: $0 <nombreOvS> <InterfacesAConectar>"
    echo "Ejemplo: $0 br-int ens4"
    exit 1
fi

NOMBRE_OVS=$1
INTERFACES=$2

echo "=== Inicializando Worker ==="
echo "OvS: $NOMBRE_OVS"
echo "Interfaces: $INTERFACES"

# Verificar existencia del bridge
bridge_exists() { ovs-vsctl br-exists "$1" 2>/dev/null; }

# Crear OvS local si no existe
if ! bridge_exists "$NOMBRE_OVS"; then
    echo "Creando bridge OvS: $NOMBRE_OVS"
    sudo ovs-vsctl add-br "$NOMBRE_OVS"
    sudo ip link set "$NOMBRE_OVS" up
else
    echo "Bridge $NOMBRE_OVS ya existe"
fi

IFS=',' read -ra INTERFACE_ARRAY <<< "$INTERFACES"
for interface in "${INTERFACE_ARRAY[@]}"; do
    interface=$(echo "$interface" | xargs)
    
    # Evitar tocar ens3 (interfaz de gestión)
    if [ "$interface" = "ens3" ]; then
        echo "Saltando $interface (interfaz de gestión)"
        continue
    fi

    if ! ip link show "$interface" &>/dev/null; then
        echo "La interfaz $interface no existe"
        continue
    fi

    if ovs-vsctl port-to-br "$interface" &>/dev/null; then
        current_bridge=$(ovs-vsctl port-to-br "$interface")
        if [ "$current_bridge" = "$NOMBRE_OVS" ]; then
            echo "Interfaz $interface ya está conectada a $NOMBRE_OVS"
            continue
        fi
    fi

    echo "Limpiando configuración IP de $interface..."
    sudo ip addr flush dev "$interface"

    echo "Conectando $interface a $NOMBRE_OVS..."
    sudo ovs-vsctl add-port "$NOMBRE_OVS" "$interface"
    sudo ip link set "$interface" up
done

echo ""
echo "=== Configuración final del bridge $NOMBRE_OVS ==="
sudo ovs-vsctl show "$NOMBRE_OVS"

echo ""
echo "✅ Worker inicializado exitosamente"
