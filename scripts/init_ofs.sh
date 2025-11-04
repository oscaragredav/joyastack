#!/bin/bash

# Script: init_ofs.sh
# Descripción: Inicializa el OFS conectando interfaces de Data Network
# Parámetros: $1=NombreOvS, $2=PuertosAConectar (separados por comas)

# Validar parámetros
if [ $# -ne 2 ]; then
    echo "Uso: $0 <NombreOvS> <PuertosAConectar>"
    echo "Ejemplo: $0 br-int ens4,ens5,ens6"
    exit 1
fi

OVS_NAME=$1
PUERTOS=$2

echo "=== Inicializando OpenFlow Switch (OFS) ==="
echo "OvS: $OVS_NAME"
echo "Puertos: $PUERTOS"

# Función para verificar si un bridge existe
bridge_exists() {
    ovs-vsctl br-exists $1 2>/dev/null
}

# Crear OvS si no existe
if ! bridge_exists $OVS_NAME; then
    echo "Creando bridge OvS: $OVS_NAME"
    sudo ovs-vsctl add-br $OVS_NAME
    if [ $? -eq 0 ]; then
        echo "Bridge $OVS_NAME creado exitosamente"
        sudo ip link set $OVS_NAME up
    else
        echo "Error al crear el bridge $OVS_NAME"
        exit 1
    fi
else
    echo "Bridge $OVS_NAME ya existe"
fi

# Configurar el bridge como OpenFlow
echo "Configurando $OVS_NAME como OpenFlow switch..."
sudo ovs-vsctl set bridge $OVS_NAME protocols=OpenFlow13

# Procesar puertos de la Data Network
IFS=',' read -ra PUERTO_ARRAY <<< "$PUERTOS"
for puerto in "${PUERTO_ARRAY[@]}"; do
    # Limpiar espacios en blanco
    puerto=$(echo $puerto | xargs)

    # Verificar si la interfaz existe
    if ! ip link show $puerto &> /dev/null; then
        echo "Advertencia: La interfaz $puerto no existe"
        continue
    fi

    echo "Procesando interfaz: $puerto"

    # Limpiar configuraciones IP en las interfaces de Data Network
    echo "Limpiando configuraciones IP de $puerto..."

    # Obtener todas las IPs configuradas
    IPS=$(ip addr show $puerto | grep 'inet ' | awk '{print $2}')

    if [ ! -z "$IPS" ]; then
        echo "Removiendo IPs existentes:"
        while IFS= read -r ip; do
            if [ ! -z "$ip" ]; then
                echo "    - $ip"
                sudo ip addr del $ip dev $puerto 2>/dev/null
            fi
        done <<< "$IPS"
    else
        echo "No hay IPs configuradas en $puerto"
    fi

    # Limpiar rutas asociadas a la interfaz
    echo "Limpiando rutas en puerto $puerto..."
    sudo ip route flush dev $puerto 2>/dev/null

    # Verificar si el puerto ya está conectado a algún bridge
    if ovs-vsctl port-to-br $puerto &> /dev/null; then
        current_bridge=$(ovs-vsctl port-to-br $puerto)
        if [ "$current_bridge" = "$OVS_NAME" ]; then
            echo "Puerto $puerto ya está conectado a $OVS_NAME"
            continue
        else
            echo "Puerto $puerto está conectado a otro bridge ($current_bridge)"
            echo "Desconectando de $current_bridge..."
            sudo ovs-vsctl del-port $current_bridge $puerto
        fi
    fi

    # Agregar interfaz al OVS
    echo "Agregando $puerto al OVS $OVS_NAME..."
    sudo ovs-vsctl add-port $OVS_NAME $puerto

    if [ $? -eq 0 ]; then
        echo "Puerto $puerto agregado exitosamente"

        sudo ip link set $puerto up

        # Configurar el puerto como trunk (permite todas las VLANs)
        echo "Configurando $puerto como trunk port..."
        sudo ovs-vsctl set port $puerto trunks=100,200,300

    else
        echo "Error al agregar $puerto"
    fi
done

# Configurar flows básicos para VLAN switching
echo ""
echo "Configurando flows básicos para switching de VLANs..."

# Limpiar flows existentes
sudo ovs-ofctl del-flows $OVS_NAME

# Flow para aprendizaje automático de MAC
sudo ovs-ofctl add-flow $OVS_NAME "priority=0,actions=normal"

echo "Flows básicos configurados"

# Habilitar STP (Spanning Tree Protocol) para evitar loops
echo "Habilitando STP..."
sudo ovs-vsctl set bridge $OVS_NAME stp_enable=true

# Mostrar configuración final
echo ""
echo "=== Configuración final del OFS ==="
echo "Bridge: $OVS_NAME"
sudo ovs-vsctl show

echo ""
echo "=== Puertos configurados ==="
sudo ovs-vsctl list port | grep -E "name|tag|trunks"

echo ""
echo "=== Flows instalados ==="
sudo ovs-ofctl dump-flows $OVS_NAME

echo ""
echo "=== OFS inicializado exitosamente ==="
