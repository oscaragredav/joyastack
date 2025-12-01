import requests
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from config.settings import WORKERS, GATEWAY, SSH_USER, SSH_PASS
from drivers.linux_drivers import create_vm_multi_vlan
from utils.logger import log_entry
from drivers.openstack_driver import OpenStackDriver
from utils.ssh import SSHConnection

PLACEMENT_API_BASE_URL = "http://localhost:8002/placement/slice"

def generate_unique_name(db, table_name: str, base_name: str) -> str:
    """
    Genera un nombre único añadiendo un sufijo numérico si es necesario.
    """
    allowed_tables = ["slice", "vm", "image", "topology"]
    if table_name not in allowed_tables:
        raise ValueError(f"Tabla no permitida: {table_name}")

    result = db.execute(
        text(f"SELECT COUNT(*) as count FROM {table_name} WHERE name LIKE :pattern"),
        {"pattern": f"{base_name}%"}
    ).mappings().first()

    count = result["count"]

    if count == 0:
        return base_name
    return f"{base_name}-{count}"


def prepare_slice_and_vms(slice_id: int, db: Session):
    """Prepara el slice y obtiene las VMs pendientes."""
    slice_obj = db.execute(
        text("SELECT * FROM slice WHERE id = :sid"),
        {"sid": slice_id}
    ).mappings().first()

    if not slice_obj:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    if slice_obj["status"] == "PENDIENTE":
        new_name = generate_unique_name(db, "slice", slice_obj["name"])
        db.execute(
            text("UPDATE slice SET name = :name WHERE id = :sid"),
            {"name": new_name, "sid": slice_id}
        )

    vms = db.execute(
        text("SELECT * FROM vm WHERE slice_id = :sid AND state = :state"),
        {"sid": slice_id, "state": "PENDIENTE"}
    ).mappings().all()

    if not vms:
        return None, None

    db.execute(
        text("UPDATE slice SET status = :status WHERE id = :sid"),
        {"status": "DESPLEGANDO", "sid": slice_id}
    )
    db.commit()
    db.expunge_all()
    print("[DeploymentManager] Nombres únicos generados para Slice y VMs.")
    log_entry(db, "DeploymentManager", "INFO", "Unique names generated for Slice and VMs", slice_id)

    for vm in vms:
        new_vm_name = generate_unique_name(db, "vm", vm["name"])
        db.execute(
            text("UPDATE vm SET name = :name WHERE id = :vid"),
            {"name": new_vm_name, "vid": vm["id"]}
        )
    db.commit()

    # Obtenemos los enlaces para la topología (necesario para OpenStack)
    links_db = db.execute(
        text("SELECT * FROM network_link WHERE slice_id = :sid"), 
        {"sid": slice_id}
    ).mappings().all()

    return slice_obj, vms, links_db


def normalize_workers(slice_id: int, db):
    """Convierte el diccionario WORKERS a formato {id: data}."""
    workers_by_id = {}
    for key, data in WORKERS.items():
        worker_num = int(key.replace('worker', ''))
        workers_by_id[worker_num] = data

    print(f"[DeploymentManager] Workers normalizados: {workers_by_id}")
    log_entry(db, "DeploymentManager", "DEBUG", f"Workers normalized: {workers_by_id}", slice_id)

    return workers_by_id


def get_placement_from_api(slice_id: int, vms: list, user_token: str, db, platform: str = "linux"):
    """Obtiene el placement óptimo desde el API de I-GA."""
    print("[DeploymentManager] Solicitando placement óptimo al algoritmo I-GA...")
    log_entry(db, "DeploymentManager", "INFO", "Requesting optimal placement from I-GA...", slice_id)


    try:
        # === MODIFICACIÓN: URL DINÁMICA SEGÚN PLATAFORMA ===
        if platform.lower() == 'openstack':
            # Endpoint específico para OpenStack
            url = f"http://localhost:8002/placement/openstack/slice/{slice_id}"
        else:
            # Endpoint original para Linux
            url = f"{PLACEMENT_API_BASE_URL}/{slice_id}"

        vms_payload = []
        for vm in vms:
            vms_payload.append({
                "id": vm["id"],
                "name": vm["name"],
                "cpu": vm["cpu"],
                "ram": vm["ram"],
                "disk": vm["disk"]
            })

        print(f"[DeploymentManager] Enviando {len(vms_payload)} VMs al algoritmo I-GA")
        print(f"[DeploymentManager] Payload: {vms_payload}")
        log_entry(db, "DeploymentManager", "DEBUG", f"Sending payload with {len(vms_payload)} VMs", slice_id)

        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {user_token}",
                "Content-Type": "application/json"
            },
            json={"vms": vms_payload},
            timeout=90
        )

        if response.status_code == 200:
            data = response.json()
            print(f"[PlacementAPI] Placement exitoso - Fitness: {data.get('fitness_score')}")
            # Reemplazar la línea de log por:
            log_entry(db, "PlacementAPI", "INFO",
                      f"Placement obtained. Fitness: {data['fitness_score']}", slice_id)
            print(f"[DeploymentManager] Respuesta del Placement API: {response.status_code}")
            log_entry(db, "DeploymentManager", "DEBUG", f"Placement API response: {response.status_code}", slice_id)

            #Para openstack
            vm_map = {}
            for p in data.get("placements", []):
                for vm_name in p.get("assigned_vms", []):
                    vm_map[vm_name] = p
            print(f"[Placement] OK. Decisión recibida para {len(vm_map)} VMs.")

            return data, vm_map
        else:
            print(f"[PlacementAPI] Error {response.status_code}")
            print("  Usando asignación round-robin como fallback")
            log_entry(db, "PlacementAPI", "WARNING",
                      f"Placement API returned {response.status_code}. Usando round-robin.", slice_id)
            return {}, None

    except requests.exceptions.RequestException as e:
        print(f"[PlacementAPI] No disponible: {e}")
        log_entry(db, "PlacementAPI", "WARNING", f"Could not connect to Placement API: {e}. Using round-robin.",
                  slice_id)
        return {}, None


def map_placements_to_workers(placement_data, workers_by_id, slice_id, db):
    """Mapea las VMs a workers según el resultado del placement."""
    vm_to_worker = {}

    if not placement_data or "placements" not in placement_data:
        return vm_to_worker

    for host_placement in placement_data["placements"]:
        host_id = host_placement.get("host_id")
        worker_id = None

        # Intentar extraer worker_id desde host_id (formato hostN)
        if isinstance(host_id, str) and host_id.startswith("host"):
            try:
                worker_id = int(host_id.replace("host", ""))
            except ValueError:
                pass

        # Intentar resolver por IP
        if worker_id is None:
            ip = host_placement.get("ip")

            if not ip:
                try:
                    hosts_list = requests.get("http://localhost:8003/hosts", timeout=60).json()
                    if isinstance(hosts_list, dict) and "hosts" in hosts_list:
                        hosts_list = hosts_list["hosts"]
                    for h in hosts_list:
                        if h.get("id") == host_id or h.get("ip") == host_id:
                            ip = h.get("ip")
                            break
                except Exception:
                    pass

            if ip:
                for wid, w in workers_by_id.items():
                    if w.get("ip") == ip or w.get("host") == ip:
                        worker_id = wid
                        break

        # Fallback determinista
        if worker_id is None:
            worker_id = (abs(hash(host_id)) % len(workers_by_id)) + 1

        # Registrar asignaciones
        for vm_name in host_placement.get("assigned_vms", []):
            vm_to_worker[vm_name] = worker_id
            print(f"[Placement] VM '{vm_name}' -> Worker {worker_id}")
            log_entry(db, "Placement", "INFO", f"VM '{vm_name}' => Worker {worker_id}", slice_id)

    return vm_to_worker


def get_vm_deployment_config(vm, slice_id, worker_id, workers_by_id, db):
    """Prepara la configuración para desplegar una VM."""
    vnc_port = (worker_id * 10000) + (slice_id % 100 * 100) + (vm["id"] % 100)
    worker_port = workers_by_id[worker_id]["ssh_port"]

    image = db.execute(
        text("SELECT * FROM image WHERE id = :iid"),
        {"iid": vm["image_id"]}
    ).mappings().first()
    image_path = image["path"] if image else "/home/ubuntu/images/cirros-0.6.2-x86_64-disk.img"

    links = db.execute(
        text("""
            SELECT vlan_id FROM network_link 
            WHERE slice_id = :sid AND (vm_a = :vid OR vm_b = :vid)
        """),
        {"sid": slice_id, "vid": vm["id"]}
    ).mappings().all()
    vlans = [l["vlan_id"] for l in links]

    return {
        "worker_port": worker_port,
        "vnc_port": vnc_port,
        "image_path": image_path,
        "vlans": vlans
    }


def deploy_vm_to_worker(vm, worker_id, config, db):
    """Despliega una VM en un worker específico."""
    print(f"[Deploy] VM '{vm['name']}' -> Worker {worker_id}")

    res = create_vm_multi_vlan(
        config["worker_port"],
        vm["name"],
        "br-int",
        config["vlans"],
        config["vnc_port"],
        vm["cpu"],
        vm["ram"],
        vm["disk"],
        vm["num_interfaces"],
        image_path=config["image_path"]
    )

    new_state = "DESPLEGADO" if res["success"] else "ERROR"
    db.execute(
        text("UPDATE vm SET state = :state, worker_id = :worker_id WHERE id = :vid"),
        {"state": new_state, "worker_id": worker_id, "vid": vm["id"]}
    )

    if res["success"] and "pid" in res:
        db.execute(
            text("UPDATE vm SET pid = :pid WHERE id = :vid"),
            {"pid": res["pid"], "vid": vm["id"]}
        )
        db.flush()

    return {
        **res,
        "vm_name": vm["name"],
        "worker_id": worker_id,
        "vnc_port": config["vnc_port"],
        "vlans": config["vlans"]
    }


def deploy_slice(slice_id: int, db: Session, user_token: str, platform: str = "linux"):
    """
    Despliega un slice utilizando el algoritmo genético de placement (I-GA)
    para asignar VMs a workers de forma óptima.
    """
    try:
        # Paso 1: Preparar slice y VMs
        slice_obj, vms, links_db = prepare_slice_and_vms(slice_id, db)
        if not vms:
            return {"message": "No hay VMs pendientes.", "results": []}

        # Paso 2: Normalizar workers
        workers_by_id = normalize_workers(slice_id, db)
        print(f"[DeploymentManager] Workers disponibles: {list(workers_by_id.keys())}")

        # Preparar lista limpia de VMs para procesos internos (OpenStack)
        vms_to_deploy_data = []
        for vm in vms:
            image = db.execute(text("SELECT path FROM image WHERE id = :iid"), {"iid": vm["image_id"]}).mappings().first()
            vms_to_deploy_data.append({
                "id": vm["id"], 
                "name": vm["name"], 
                "cpu": vm["cpu"],
                "ram": vm["ram"],
                "disk": vm["disk"],
                "image_ref": image["path"] if image else "cirros"
            })

        # ACTUALIZAR PLATAFORMA (Necesario para el Placement)
        db.execute(text("UPDATE slice SET platform = :plat WHERE id = :sid"), {"plat": platform, "sid": slice_id})
        db.commit()    

        # Paso 3: Obtener placement del algoritmo I-GA
        placement_data, placement_map = get_placement_from_api(slice_id, vms, user_token, db, platform)

        # Paso 4: Desplegar VMs
        results = []

        # =================================================================
        # === BLOQUE LÓGICO PARA OPENSTACK ===
        # =================================================================
        if platform.lower() == 'openstack':
            print(f"[Deploy] Iniciando OpenStack para Slice {slice_id}")
            os_driver = OpenStackDriver()

            # Variable para controlar si debemos hacer rollback
            deployment_failed = False
            error_message = ""
            
            try:
                # a. Crear Proyecto y Topología Base
                project_id = os_driver.create_project(slice_obj['name'])
                base_topo = os_driver.create_topology(project_id, slice_obj['name'])
                
                # b. Gestión de Puertos y Redes
                vm_ports_map = {vm['id']: [] for vm in vms_to_deploy_data}
                
                # Puertos de Gestión (Internet/SSH)
                for vm in vms_to_deploy_data:
                    p_id = os_driver.create_port(base_topo['mgmt_network_id'], project_id, base_topo['mgmt_sec_group_id'], f"mgmt_{vm['name']}")
                    vm_ports_map[vm['id']].append(p_id)

                # Puertos de Topología L2 (Enlaces internos)
                for i, link in enumerate(links_db):
                    l2_net_id = os_driver.create_l2_network(project_id, f"L2_{link['source_vm_id']}_{link['target_vm_id']}")
                    if link['source_vm_id'] in vm_ports_map:
                        vm_ports_map[link['source_vm_id']].append(os_driver.create_port(l2_net_id, project_id, name=f"link_{i}_src"))
                    if link['target_vm_id'] in vm_ports_map:
                        vm_ports_map[link['target_vm_id']].append(os_driver.create_port(l2_net_id, project_id, name=f"link_{i}_dst"))

                # c. Despliegue de Instancias
                for vm in vms_to_deploy_data:
                    # Obtener Zona de Disponibilidad desde Placement (R4)
                    vm_decision = placement_map.get(vm["name"], {})
                    target_az = vm_decision.get("availability_zone", "nova") 
                    
                    res = os_driver.deploy_vm(
                        project_id=project_id,
                        vm_name=vm["name"],
                        image_ref=vm["image_ref"],
                        cpus=vm["cpu"],
                        ram_mb=int(vm["ram"] * 1024),
                        port_ids=vm_ports_map[vm['id']],
                        availability_zone=target_az 
                    )
                    db.execute(text("UPDATE vm SET console_access = :ip WHERE id = :vid"), {"ip": res["ip"], "vid": vm["id"]})
                    results.append({**res, "vm_name": vm["name"], "availability_zone": target_az})

            except Exception as e:
                # !!! LÓGICA DE ROLLBACK !!!
                deployment_failed = True
                error_message = str(e)
                print(f"[Deploy] ERROR CRÍTICO en OpenStack: {e}")
                print(f"[Deploy] Iniciando ROLLBACK automático para Slice {slice_id}...")
                
                # Borrar todo lo creado hasta ahora
                try:
                    os_driver.delete_slice_resources(slice_obj['name'])
                    print("[Deploy] Rollback completado exitosamente.")
                except Exception as cleanup_error:
                    print(f"[Deploy] Error durante el rollback: {cleanup_error}")

            # Si falló, lanzamos error para detener todo y notificar al usuario
            if deployment_failed:
                db.execute(text("UPDATE slice SET status = 'ERROR' WHERE id = :sid"), {"sid": slice_id})
                db.commit()
                raise HTTPException(status_code=500, detail=f"Fallo despliegue OpenStack (Rollback ejecutado): {error_message}")        

        # =================================================================
        # === BLOQUE LINUX ===
        # =================================================================
        else:
            # Para Linux
            vm_to_worker = map_placements_to_workers(placement_data, workers_by_id, slice_id, db)
            for i, vm in enumerate(vms):
                # Determinar worker (algoritmo o round-robin)
                print(f"\n[DeploymentManager] Desplegando VM: {vm['name']}")
                log_entry(db, "DeploymentManager", "INFO", f"Deploying VM: {vm['name']}", slice_id)

                if vm["name"] in vm_to_worker:
                    worker_id = vm_to_worker[vm["name"]]
                    print(f"  ✓ Usando I-GA: Worker {worker_id}")
                else:
                    worker_id = (i % len(workers_by_id)) + 1
                    print(f"  ⚠ Usando asignación round-robin: Worker {worker_id}")
                    log_entry(db, "DeploymentManager", "WARNING", f"Using round-robin: Worker {worker_id}", slice_id)

                # Validar worker
                if worker_id not in workers_by_id:
                    print(f"  ✗ Worker {worker_id} no existe. Usando Worker 1 como fallback")
                    log_entry(db, "DeploymentManager", "ERROR", f"Worker {worker_id} does not exist. Using Worker 1.", slice_id)
                    worker_id = 1

                if worker_id not in workers_by_id:
                    raise Exception(f"Worker {worker_id} no configurado")

                # Preparar y desplegar
                config = get_vm_deployment_config(vm, slice_id, worker_id, workers_by_id, db)
                result = deploy_vm_to_worker(vm, worker_id, config, db)
                results.append(result)


        # Paso 5: Finalizar
        db.execute(
            text("UPDATE slice SET status = :status WHERE id = :sid"),
            {"status": "DESPLEGADO", "sid": slice_id}
        )
        db.commit()

        # Construir respuesta
        response = {
            "slice_id": slice_id,
            "slice_name": slice_obj["name"],
            "vms_deployed": len(results),
            "results": results
        }

        if placement_data:
            response["placement_metrics"] = {
                "total_energy": placement_data["total_energy"],
                "total_availability": placement_data["total_availability"],
                "fitness_score": placement_data["fitness_score"],
                "algorithm": "I-GA (Improved Genetic Algorithm)"
            }
        else:
            response["placement_metrics"] = {
                "algorithm": "Round-Robin (fallback)",
                "reason": "Placement API no disponible"
            }

        return response

    except HTTPException as he:
        # Re-lanzar excepciones HTTP ya controladas
        raise he
    except Exception as e:
        db.rollback()
        log_entry(db, "DeploymentManager", "ERROR", str(e), slice_id)
        # Si ocurre un error no controlado fuera del bloque OpenStack
        db.execute(text("UPDATE slice SET status = 'ERROR' WHERE id = :sid"), {"sid": slice_id})
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))

# === FUNCIÓN DE BORRADO ===
def delete_slice_resources(slice_id: int, db: Session):
    try:
        slice_obj = db.execute(text("SELECT * FROM slice WHERE id = :sid"), {"sid": slice_id}).mappings().first()
        if not slice_obj: raise HTTPException(status_code=404, detail="Slice no encontrado")

        platform = slice_obj.get("platform", "linux") 
        print(f"[Delete] Eliminando Slice {slice_id} en plataforma: {platform}")

        if platform == 'openstack':
            os_driver = OpenStackDriver()
            os_driver.delete_slice_resources(slice_obj['name'])
        else:
            # Obtener VMs del slice
            vms = db.execute(
                text("""
                    SELECT v.id, v.name, v.cpu, v.ram, v.disk, w.ip as worker_ip, w.id as worker_id
                    FROM vm v
                    JOIN worker w ON v.worker_id = w.id
                    WHERE v.slice_id = :sid
                """),
                {"sid": slice_id},
            ).mappings().all()

            if not vms:
                return {"status": "not_found", "message": "No hay VMs en este slice"}

            log_entry(db, "SliceManager", "INFO", f"Eliminando slice {slice_id} con {len(vms)} VMs")

            for vm in vms:
                wip = vm["worker_ip"]
                ssh_port = None
                for name, data in WORKERS.items():
                    print(f"[SliceManager] Comprobando worker {name} ({data['ip']})")
                    if data["ip"] == wip:
                        ssh_port = data["ssh_port"]
                        print(f"[SliceManager] Conectando a worker {name} ({wip}:{ssh_port}) para eliminar VM")
                        break
                if not ssh_port:
                    continue

                conn = SSHConnection(GATEWAY, ssh_port, SSH_USER, SSH_PASS)
                if conn.connect():
                    try:
                        # Matar proceso QEMU si existiese
                        print(f"[SliceManager] Eliminando VM {vm['name']} en worker {wip}")
                        conn.exec_sudo(f"pkill -f 'qemu-system.*{vm["name"]}' || true")
                        conn.exec_sudo(f"sleep 1")
                        # Limpiar TAPs y OvS
                        conn.exec_sudo(
                            f"ovs-vsctl list-ports br-int | grep {vm["name"]} | xargs -r -I{{}} ovs-vsctl del-port br-int {{}}")
                        conn.exec_sudo(f"ip link del $(ip link show | grep VM_Auto_ | cut -d: -f2) 2>/dev/null || true")
                        log_entry(db, "SliceManager", "INFO", f"Limpieza de VM en worker {wip} completada")
                    except Exception as e:
                        print(f"Error creando slice: {e}")
                        log_entry(db, "SliceManager", "ERROR", f"Error limpiando worker {wip}: {e}", slice_id)
                    finally:
                        conn.close()

        db.execute(text("UPDATE slice SET status = 'TERMINATED' WHERE id = :sid"), {"sid": slice_id})
        db.commit()
        return {"status": "success", "message": f"Slice {slice_id} eliminado"}

    except Exception as e:
        print(f"Error eliminando slice: {e}")
        db.rollback()
        log_entry(db, "DeploymentManager", "ERROR", str(e), slice_id)
        raise HTTPException(status_code=500, detail=str(e))        