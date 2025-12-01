import openstack
import base64
import time
from config.settings import OPENSTACK_CONFIG

class OpenStackDriver:
    """
    Driver principal para la orquestación de recursos en OpenStack.
    
    Responsabilidades:
    - Gestión del ciclo de vida de Proyectos (Tenants), Redes, Routers y VMs.
    - Implementación de topologías de red (Estrella, L2 puro, VLANs).
    - Abstracción de la API de OpenStack (SDK) para el orquestador.
    
    Cumple con:
    - R3: Uso correcto y eficiente de APIs (Autenticación, Gestión de errores).
    - R5: Networking avanzado (VLANs, Provider Networks, Security Groups).
    """

    def __init__(self, project_id=None):
        """
        Inicializa la conexión con la API de OpenStack usando credenciales de administrador.
        
        Args:
            project_id (str, optional): ID del proyecto objetivo. Si es None, opera a nivel global/admin.
        
        Nota:
            Lee la configuración desde config.settings.OPENSTACK_CONFIG.
        """
        print("[OpenStackDriver] Inicializando conexión...")
        self.conn = openstack.connect(
            auth_url=OPENSTACK_CONFIG['auth_url'],
            project_name=OPENSTACK_CONFIG['project_name'],
            username=OPENSTACK_CONFIG['username'],
            password=OPENSTACK_CONFIG['password'],
            user_domain_name=OPENSTACK_CONFIG['user_domain_name'],
            project_domain_name=OPENSTACK_CONFIG['project_domain_name'],
        )
        self.target_project_id = project_id

    def create_project(self, slice_name, description="Slice creado por Orchestrator"):
        """
        Crea un nuevo Proyecto (Tenant) aislado para un Slice específico.
        
        Args:
            slice_name (str): Nombre del slice (debe ser único).
            description (str): Descripción para metadatos.
            
        Returns:
            str: ID del proyecto creado (UUID).
            
        Raises:
            Exception: Si falla la creación (ej. nombre duplicado o error de Keystone).
        """
        try:
            project = self.conn.identity.create_project(
                name=slice_name,
                description=description,
                domain_id="default"
            )
            print(f"[OpenStackDriver] Proyecto creado: {project.name} ({project.id})")
            return project.id
        except Exception as e:
            print(f"[OpenStackDriver] Error creando proyecto: {e}")
            raise e

    def create_topology(self, project_id, slice_name):
        """
        Despliega la Topología Base de Gestión para el Slice.
        
        Cumplimiento R5 (Networking):
        - Crea una Provider Network tipo VLAN para minimizar overhead (sin túneles).
        - Crea un Router con SNAT para dar salida a Internet a las VMs.
        - Configura DHCP y Security Groups básicos.

        Args:
            project_id (str): ID del proyecto donde se crearán los recursos.
            slice_name (str): Nombre base para etiquetar recursos (net_X, router_X).

        Returns:
            dict: Diccionario con IDs clave {'mgmt_network_id', 'mgmt_sec_group_id'}.
        """
        try:
            # 1. Red de Gestión (Provider VLAN)
            # Al especificar provider:network_type='vlan', evitamos túneles (Overhead).
            net = self.conn.network.create_network(
                name=f"mgmt_{slice_name}",
                project_id=project_id,
                is_admin_state_up=True,
                provider_network_type='vlan',
                provider_physical_network=OPENSTACK_CONFIG['physical_network']
                # Nota: No fijamos segmentation_id, dejamos que Neutron asigne una VLAN libre.
            )
            
            # 2. Subred Mgmt (con DHCP)
            subnet = self.conn.network.create_subnet(
                name=f"subnet_mgmt_{slice_name}",
                network_id=net.id,
                project_id=project_id,
                ip_version=4,
                cidr="10.60.6.0/24",
                gateway_ip="10.60.6.1",
                dns_nameservers=["8.8.8.8"]
            )

            # 3. Router (Salida a Internet)
            router = self.conn.network.create_router(
                name=f"router_{slice_name}",
                project_id=project_id,
                external_gateway_info={
                    "network_id": self._get_external_network_id(),
                    "enable_snat": True 
                }
            )
            self.conn.network.add_interface_to_router(router.id, subnet_id=subnet.id)
            
            # 4. Security Group
            sec_group = self.conn.network.create_security_group(
                name=f"sg_{slice_name}",
                project_id=project_id
            )
            for proto in ['tcp', 'icmp']:
                pr = 22 if proto == 'tcp' else None
                self.conn.network.create_security_group_rule(
                    security_group_id=sec_group.id,
                    direction='ingress', protocol=proto, 
                    port_range_min=pr, port_range_max=pr, 
                    project_id=project_id
                )

            return {
                "mgmt_network_id": net.id,
                "mgmt_sec_group_id": sec_group.id
            }

        except Exception as e:
            print(f"[OpenStackDriver] Error topología base: {e}")
            raise e

    def create_l2_network(self, project_id, name_suffix):
        """
        Crea una red de Capa 2 (L2) pura, mapeada a una VLAN física.
        
        Características:
        - Sin Subred (No DHCP, No Gateway).
        - Equivale a un cable virtual o switch aislado entre VMs.
        - Usado para enlaces internos de topologías complejas (Anillo, Bus).
        
        Args:
            project_id (str): Proyecto dueño de la red.
            name_suffix (str): Sufijo para el nombre (ej. "link_A_B").
            
        Returns:
            str: Network ID.
        """
        net = self.conn.network.create_network(
            name=f"link_{name_suffix}", 
            project_id=project_id,
            provider_network_type='vlan',
            provider_physical_network=OPENSTACK_CONFIG['physical_network']
        )
        print(f"[OpenStackDriver] Link L2 (VLAN) creado: {net.name}")
        return net.id

    def create_port(self, network_id, project_id, sec_group_id=None, name="port"):
        """
        Crea un puerto de red (interfaz virtual) asociado a una red específica.
        
        Args:
            network_id (str): ID de la red donde vivirá el puerto.
            project_id (str): ID del proyecto.
            sec_group_id (str, optional): ID del Security Group a aplicar.
            name (str): Nombre identificativo del puerto.
            
        Returns:
            str: Port ID.
        """
        args = {
            "name": name,
            "network_id": network_id,
            "project_id": project_id,
            "admin_state_up": True
        }
        if sec_group_id:
            args["security_groups"] = [sec_group_id]
            
        port = self.conn.network.create_port(**args)
        return port.id

    def deploy_vm(self, project_id, vm_name, image_ref, cpus, ram_mb, port_ids, availability_zone):
        """
        Despliega una Instancia (VM) en OpenStack.
        
        Funcionalidades:
        - Selección inteligente de imagen (búsqueda por nombre o fallback).
        - Creación dinámica de Flavors (si no existe uno con la CPU/RAM exacta).
        - Inyección de script 'User Data' para auto-configurar interfaces de red.
        - Soporte multi-interfaz mediante lista de puertos pre-creados.
        
        Args:
            project_id (str): Proyecto dueño de la VM.
            vm_name (str): Nombre de la VM.
            image_ref (str): Ruta o nombre de la imagen SO.
            cpus (int): Número de vCPUs.
            ram_mb (int): Memoria RAM en MB.
            port_ids (list): Lista de IDs de puertos a conectar (eth0, eth1...).
            availability_zone (str): Zona de disponibilidad (Placement).
            
        Returns:
            dict: {id, status, ip} de la VM creada.
        """
        try:
            clean_image_name = image_ref.split('/')[-1].split('.')[0]
            if "cirros" in clean_image_name: clean_image_name = "cirros"
            image = self.conn.compute.find_image(clean_image_name) or list(self.conn.compute.images())[0]
            flavor = self._get_or_create_flavor(cpus, ram_mb)
            nics = [{"port-id": pid} for pid in port_ids]
            
            # Script Bash que se ejecuta al primer booteo (cloud-init)
            # Levanta todas las interfaces, pero solo pide DHCP donde hay respuesta (Mgmt)
            user_data_script = """#!/bin/sh
            for iface in $(ip link show | grep '^[0-9]' | awk -F: '{print $2}' | grep -v 'lo'); do
                ip link set dev $iface up
                timeout 5s dhclient $iface || echo "L2 link"
            done
            """
            user_data_b64 = base64.b64encode(user_data_script.encode('utf-8')).decode('utf-8')
            
            server = self.conn.compute.create_server(
                name=vm_name, image_id=image.id, flavor_id=flavor.id, networks=nics,
                availability_zone=availability_zone, project_id=project_id, user_data=user_data_b64
            )
            server = self.conn.compute.wait_for_server(server)
            
            # Buscar IP de gestión (usualmente en la subred 192.168.100.x)
            ip_address = "0.0.0.0"
            for _, addrs in server.addresses.items():
                for addr in addrs:
                    if addr['version'] == 4:
                        ip_address = addr['addr']
                        if "10.60.6" in ip_address: break
            return {"id": server.id, "status": "ACTIVE", "ip": ip_address}
        except Exception as e:
            raise e

    def delete_slice_resources(self, slice_name):
        """
        Realiza la eliminación en cascada de todos los recursos de un Slice.
        
        Orden de limpieza (para evitar errores de dependencia):
        1. VMs (Servers) -> Libera puertos Compute.
        2. Router Interfaces -> Desconecta subredes del router.
        3. Routers -> Elimina la capa L3.
        4. Ports -> Elimina puertos huérfanos.
        5. Networks -> Elimina capas L2.
        6. Security Groups.
        7. Project -> Elimina el contenedor lógico.
        
        Args:
            slice_name (str): Nombre del slice (coincide con el nombre del proyecto).
            
        Returns:
            bool: True si fue exitoso o no existía.
        """
        try:
            project = self.conn.identity.find_project(slice_name, domain_id="default")
            if not project: return True
            project_id = project.id

            servers = self.conn.compute.servers(all_tenants=True, project_id=project_id)
            for server in servers:
                self.conn.compute.delete_server(server.id)
                self.conn.compute.wait_for_delete(server)

            routers = self.conn.network.routers(project_id=project_id)
            for router in routers:
                ports = self.conn.network.ports(device_id=router.id)
                for port in ports:
                    if port.device_owner == "network:router_interface":
                        self.conn.network.remove_interface_from_router(router.id, port_id=port.id)
                self.conn.network.delete_router(router.id)

            ports = self.conn.network.ports(project_id=project_id)
            for port in ports: self.conn.network.delete_port(port.id)

            networks = self.conn.network.networks(project_id=project_id)
            for net in networks: self.conn.network.delete_network(net.id)

            sgs = self.conn.network.security_groups(project_id=project_id)
            for sg in sgs:
                if sg.name != "default": self.conn.network.delete_security_group(sg.id)

            self.conn.identity.delete_project(project_id)
            print(f"[OpenStackDriver] Proyecto {slice_name} eliminado completamente.")
            return True
        except Exception as e:
            print(f"[OpenStackDriver] Error crítico borrando slice: {e}")
            raise e

    def _get_external_network_id(self):
        """
        Obtiene el ID de la red externa (Provider Network) configurada para salida a Internet.
        """
        ext_net = self.conn.network.find_network(OPENSTACK_CONFIG["external_network"])
        if not ext_net:
            for net in self.conn.network.networks(is_router_external=True): return net.id
            raise Exception("Falta red externa")
        return ext_net.id

    def _get_or_create_flavor(self, cpus, ram_mb):
        """
        Busca o crea un 'Flavor' (plantilla de hardware) que coincida con los requisitos.
        Permite flexibilidad total en la definición de recursos del usuario.
        """
        flavor_name = f"custom-{cpus}cpu-{ram_mb}mb"
        try:
            flavor = self.conn.compute.find_flavor(flavor_name)
            if not flavor:
                flavor = self.conn.compute.create_flavor(name=flavor_name, ram=ram_mb, vcpus=cpus, disk=10)
            return flavor
        except:
            return self.conn.compute.find_flavor("m1.small")