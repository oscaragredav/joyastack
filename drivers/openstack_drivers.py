from novaclient import client as nova_client
from neutronclient.v2_0 import client as neutron_client
import os
import time


class OpenStackConnection:
    """Gestiona la conexión con OpenStack."""

    def __init__(self, auth_url: str = None, username: str = None, password: str = None,
                 project_name: str = None, user_domain_name: str = "Default",
                 project_domain_name: str = "Default"):
        """
        Inicializa la conexión con OpenStack.
        Puede tomar credenciales de variables de entorno o parámetros.
        """
        self.auth_url = auth_url or os.getenv("OS_AUTH_URL")
        self.username = username or os.getenv("OS_USERNAME")
        self.password = password or os.getenv("OS_PASSWORD")
        self.project_name = project_name or os.getenv("OS_PROJECT_NAME")
        self.user_domain_name = user_domain_name or os.getenv("OS_USER_DOMAIN_NAME", "Default")
        self.project_domain_name = project_domain_name or os.getenv("OS_PROJECT_DOMAIN_NAME", "Default")

        self.nova = None
        self.neutron = None

    def connect(self):
        """Establece conexión con Nova y Neutron."""
        try:
            self.nova = nova_client.Client(
                "2.1",
                username=self.username,
                password=self.password,
                project_name=self.project_name,
                auth_url=self.auth_url,
                user_domain_name=self.user_domain_name,
                project_domain_name=self.project_domain_name
            )

            self.neutron = neutron_client.Client(
                username=self.username,
                password=self.password,
                project_name=self.project_name,
                auth_url=self.auth_url,
                user_domain_name=self.user_domain_name,
                project_domain_name=self.project_domain_name
            )

            print(f"[OpenStackDriver] Conectado a {self.auth_url}")
            return True

        except Exception as e:
            print(f"[OpenStackDriver] Error de conexión: {e}")
            raise e


def create_vm_multi_vlan(worker_port: int, vm_name: str, bridge: str, vlans: list,
                         vnc_port: int, cpus: int, ram_mb: int, disk_gb: int,
                         num_ifaces: int, image_path: str,
                         flavor_name: str = None, network_name: str = None) -> dict:
    """
    Crea una VM en OpenStack con múltiples VLANs.

    Args:
        worker_port: Puerto del worker (en OpenStack puede ser el ID del compute node)
        vm_name: Nombre de la VM
        bridge: Bridge (en OpenStack se ignora, se usa la red de Neutron)
        vlans: Lista de VLANs a asignar
        vnc_port: Puerto VNC (OpenStack lo gestiona automáticamente)
        cpus: Número de CPUs
        ram_mb: RAM en MB
        disk_gb: Disco en GB
        num_ifaces: Número de interfaces
        image_path: Ruta de la imagen (en OpenStack es el nombre/ID de la imagen en Glance)
        flavor_name: Nombre del flavor (si None, se crea uno custom)
        network_name: Red base de Neutron
    """

    conn = OpenStackConnection()
    conn.connect()

    try:
        # ============================================
        # 1. Obtener o crear flavor
        # ============================================
        flavor = None
        flavor_name = flavor_name or f"custom-{cpus}vcpu-{ram_mb}mb-{disk_gb}gb"

        try:
            flavor = conn.nova.flavors.find(name=flavor_name)
            print(f"[OpenStackDriver] Usando flavor existente: {flavor_name}")
        except Exception:
            print(f"[OpenStackDriver] Creando flavor: {flavor_name}")
            flavor = conn.nova.flavors.create(
                name=flavor_name,
                ram=ram_mb,
                vcpus=cpus,
                disk=disk_gb
            )

        # ============================================
        # 2. Obtener imagen
        # ============================================
        # image_path puede ser nombre de imagen o ID
        try:
            if image_path.startswith("http"):
                # Si es URL, usar el nombre de archivo como referencia
                image_name = image_path.split("/")[-1].split(".")[0]
                image = conn.nova.glance.find_image(image_name)
            else:
                # Buscar por nombre o ID
                image = conn.nova.glance.find_image(image_path)
            print(f"[OpenStackDriver] Usando imagen: {image.name}")
        except Exception as e:
            print(f"[OpenStackDriver] ERROR: Imagen '{image_path}' no encontrada: {e}")
            return {
                "worker_port": worker_port,
                "vm_name": vm_name,
                "stdout": "",
                "stderr": f"Imagen no encontrada: {image_path}",
                "success": False,
                "pid": None,
                "vlans": vlans,
                "instance_id": None
            }

        # ============================================
        # 3. Configurar redes y VLANs
        # ============================================
        nics = []

        if not vlans or vlans == [0]:
            # Sin VLANs: usar red por defecto
            networks = conn.neutron.list_networks()["networks"]
            default_net = next((n for n in networks if not n.get("router:external")), None)
            if default_net:
                nics.append({"net-id": default_net["id"]})
                print(f"[OpenStackDriver] Usando red por defecto: {default_net['name']}")
        else:
            # Con VLANs: buscar/crear subredes VLAN
            for vlan_id in vlans:
                network = get_or_create_vlan_network(conn, vlan_id, network_name)
                if network:
                    nics.append({"net-id": network["id"]})
                    print(f"[OpenStackDriver] Agregada red VLAN {vlan_id}: {network['name']}")

        if not nics:
            print("[OpenStackDriver] ERROR: No se configuraron redes")
            return {
                "worker_port": worker_port,
                "vm_name": vm_name,
                "stdout": "",
                "stderr": "No se pudieron configurar las redes",
                "success": False,
                "pid": None,
                "vlans": vlans,
                "instance_id": None
            }

        # ============================================
        # 4. Crear instancia
        # ============================================
        print(f"[OpenStackDriver] Creando VM '{vm_name}' con {len(nics)} interfaces")

        server = conn.nova.servers.create(
            name=vm_name,
            image=image.id,
            flavor=flavor.id,
            nics=nics,
            availability_zone=f"nova:compute-{worker_port}" if worker_port else None
        )

        print(f"[OpenStackDriver] VM creada con ID: {server.id}")

        # ============================================
        # 5. Esperar a que esté activa
        # ============================================
        timeout = 120
        start_time = time.time()

        while time.time() - start_time < timeout:
            server = conn.nova.servers.get(server.id)
            status = server.status

            if status == "ACTIVE":
                print(f"[OpenStackDriver] VM '{vm_name}' ACTIVA")
                break
            elif status == "ERROR":
                print(f"[OpenStackDriver] VM '{vm_name}' en ERROR")
                return {
                    "worker_port": worker_port,
                    "vm_name": vm_name,
                    "stdout": f"VM en estado ERROR",
                    "stderr": str(server.fault),
                    "success": False,
                    "pid": None,
                    "vlans": vlans,
                    "instance_id": server.id
                }

            time.sleep(2)

        # Obtener info final
        server = conn.nova.servers.get(server.id)

        # Obtener IPs
        ips = []
        for network_name, addresses in server.networks.items():
            ips.extend([addr["addr"] for addr in addresses])

        # En OpenStack el "PID" sería el instance ID
        return {
            "worker_port": worker_port,
            "vm_name": vm_name,
            "stdout": f"VM creada: {server.id}\nIPs: {', '.join(ips)}\nEstado: {server.status}",
            "stderr": "",
            "success": server.status == "ACTIVE",
            "pid": server.id,  # Usamos instance_id como "PID"
            "vlans": vlans,
            "instance_id": server.id,
            "ips": ips,
            "status": server.status
        }

    except Exception as e:
        print(f"[OpenStackDriver] ERROR: {e}")
        return {
            "worker_port": worker_port,
            "vm_name": vm_name,
            "stdout": "",
            "stderr": str(e),
            "success": False,
            "pid": None,
            "vlans": vlans,
            "instance_id": None
        }


def get_or_create_vlan_network(conn, vlan_id: int, base_network_name: str = None):
    """
    Busca o crea una red con la VLAN especificada en OpenStack.
    """
    network_name = f"vlan-{vlan_id}"

    try:
        # Buscar red existente
        networks = conn.neutron.list_networks(name=network_name)["networks"]
        if networks:
            return networks[0]

        # Crear nueva red VLAN
        print(f"[OpenStackDriver] Creando red VLAN {vlan_id}")

        network_body = {
            "network": {
                "name": network_name,
                "admin_state_up": True,
                "provider:network_type": "vlan",
                "provider:segmentation_id": vlan_id
            }
        }

        if base_network_name:
            network_body["network"]["provider:physical_network"] = base_network_name

        network = conn.neutron.create_network(body=network_body)["network"]

        # Crear subnet para la VLAN
        subnet_body = {
            "subnet": {
                "name": f"subnet-vlan-{vlan_id}",
                "network_id": network["id"],
                "ip_version": 4,
                "cidr": f"192.168.{vlan_id}.0/24",
                "enable_dhcp": True
            }
        }
        conn.neutron.create_subnet(body=subnet_body)

        print(f"[OpenStackDriver] Red VLAN {vlan_id} creada exitosamente")
        return network

    except Exception as e:
        print(f"[OpenStackDriver] Error creando red VLAN {vlan_id}: {e}")
        return None


def delete_vm(instance_id: str) -> dict:
    """Elimina una VM de OpenStack."""
    conn = OpenStackConnection()
    conn.connect()

    try:
        server = conn.nova.servers.get(instance_id)
        server.delete()
        print(f"[OpenStackDriver] VM {instance_id} eliminada")
        return {"success": True, "instance_id": instance_id}
    except Exception as e:
        print(f"[OpenStackDriver] Error eliminando VM: {e}")
        return {"success": False, "error": str(e)}


def get_vm_status(instance_id: str) -> dict:
    """Obtiene el estado de una VM."""
    conn = OpenStackConnection()
    conn.connect()

    try:
        server = conn.nova.servers.get(instance_id)
        return {
            "instance_id": instance_id,
            "name": server.name,
            "status": server.status,
            "ips": [addr["addr"] for addrs in server.networks.values() for addr in addrs]
        }
    except Exception as e:
        print(f"[OpenStackDriver] Error obteniendo estado: {e}")
        return {"error": str(e)}