import requests
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from config.settings import WORKERS
from drivers.linux_drivers import create_vm_multi_vlan


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

    for vm in vms:
        new_vm_name = generate_unique_name(db, "vm", vm["name"])
        db.execute(
            text("UPDATE vm SET name = :name WHERE id = :vid"),
            {"name": new_vm_name, "vid": vm["id"]}
        )
    db.commit()

    return slice_obj, vms


def normalize_workers():
    """Convierte el diccionario WORKERS a formato {id: data}."""
    workers_by_id = {}
    for key, data in WORKERS.items():
        worker_num = int(key.replace('worker', ''))
        workers_by_id[worker_num] = data
    return workers_by_id


def get_placement_from_api(slice_id: int, vms: list, user_token: str):
    """Obtiene el placement óptimo desde el API de I-GA."""
    try:
        vms_payload = []
        for vm in vms:
            vms_payload.append({
                "id": vm["id"],
                "name": vm["name"],
                "cpu": vm["cpu"],
                "ram": vm["ram"],
                "disk": vm["disk"]
            })

        print(f"[PlacementAPI] Enviando {len(vms_payload)} VMs al algoritmo I-GA")

        response = requests.post(
            f"http://localhost:8002/placement/slice/{slice_id}",
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
            return data
        else:
            print(f"[PlacementAPI] Error {response.status_code}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"[PlacementAPI] No disponible: {e}")
        return None


def map_placements_to_workers(placement_data, workers_by_id):
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


def deploy_slice(slice_id: int, db: Session, user_token: str):
    """
    Despliega un slice utilizando el algoritmo genético de placement (I-GA)
    para asignar VMs a workers de forma óptima.
    """
    try:
        # Paso 1: Preparar slice y VMs
        slice_obj, vms = prepare_slice_and_vms(slice_id, db)
        if not vms:
            return {"message": "No hay VMs pendientes.", "results": []}

        # Paso 2: Normalizar workers
        workers_by_id = normalize_workers()
        print(f"[DeploymentManager] Workers disponibles: {list(workers_by_id.keys())}")

        # Paso 3: Obtener placement del algoritmo I-GA
        placement_data = get_placement_from_api(slice_id, vms, user_token)
        vm_to_worker = map_placements_to_workers(placement_data, workers_by_id)

        # Paso 4: Desplegar VMs
        results = []
        for i, vm in enumerate(vms):
            # Determinar worker (algoritmo o round-robin)
            if vm["name"] in vm_to_worker:
                worker_id = vm_to_worker[vm["name"]]
                print(f"  ✓ Usando I-GA: Worker {worker_id}")
            else:
                worker_id = (i % len(workers_by_id)) + 1
                print(f"  ⚠ Usando round-robin: Worker {worker_id}")

            # Validar worker
            if worker_id not in workers_by_id:
                print(f"  ✗ Worker {worker_id} inválido, usando Worker 1")
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

    except Exception as e:
        print(f"[DeploymentManager] ERROR: {e}")
        db.rollback()
        raise e