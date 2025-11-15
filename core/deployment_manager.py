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


def deploy_slice(slice_id: int, db: Session, user_token: str):
    """
    Despliega un slice utilizando el algoritmo genético de placement (I-GA)
    para asignar VMs a workers de forma óptima.
    """

    try:
        # ============================================
        # PASO 1: Preparar slice y VMs pendientes
        # ============================================
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
            return {"message": "No hay VMs pendientes.", "results": []}

        db.execute(
            text("UPDATE slice SET status = :status WHERE id = :sid"),
            {"status": "DESPLEGANDO", "sid": slice_id}
        )

        db.commit()
        db.expunge_all()
        print("[DeploymentManager] Nombres únicos generados para Slice y VMs.")

        for vm in vms:
            new_vm_name = generate_unique_name(db, "vm", vm["name"])
            db.execute(
                text("UPDATE vm SET name = :name WHERE id = :vid"),
                {"name": new_vm_name, "vid": vm["id"]}
            )
        db.commit()

        # ============================================
        # NORMALIZAR WORKERS: Crear mapeo int->dict
        # ============================================
        workers_by_id = {}
        for key, data in WORKERS.items():
            # Extraer número del worker (worker1 -> 1)
            worker_num = int(key.replace('worker', ''))
            workers_by_id[worker_num] = data

        print(f"[DeploymentManager] Workers normalizados: {workers_by_id}")

        # ============================================
        # PASO 2: Obtener placement óptimo desde API
        # ============================================
        placement_data = None
        vm_to_worker = {}

        try:
            print("[DeploymentManager] Solicitando placement óptimo al algoritmo I-GA...")

            # Enviar las VMs en el body del request
            vms_payload = []
            for vm in vms:
                vms_payload.append({
                    "id": vm["id"],
                    "name": vm["name"],
                    "cpu": vm["cpu"],
                    "ram": vm["ram"],
                    "disk": vm["disk"]
                })

            print(f"[DeploymentManager] Enviando payload con {len(vms_payload)} VMs al Placement API")
            print(f"[DeploymentManager] Payload: {vms_payload}")

            placement_response = requests.post(
                f"http://localhost:8002/placement/slice/{slice_id}",
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Content-Type": "application/json"
                },
                json={"vms": vms_payload},
                timeout=90
            )

            print(f"[DeploymentManager] Respuesta del Placement API: {placement_response.status_code}")

            if placement_response.status_code == 200:
                placement_data = placement_response.json()
                print(f"[DeploymentManager] Placement obtenido con éxito:")
                print(f"  - Energía total: {placement_data['total_energy']}")
                print(f"  - Disponibilidad: {placement_data['total_availability']}")
                print(f"  - Fitness score: {placement_data['fitness_score']}")

                if "placements" in placement_data:
                    for host_placement in placement_data["placements"]:
                        host_id = host_placement.get("host_id")
                        worker_id = None

                        # 1) Si host_id tiene formato hostN
                        if isinstance(host_id, str) and host_id.startswith("host"):
                            try:
                                worker_id = int(host_id.replace("host", ""))
                            except ValueError:
                                worker_id = None

                        # 2) Intentar resolver por IP
                        if worker_id is None:
                            ip = host_placement.get("ip")

                            if not ip:
                                try:
                                    hosts_list = requests.get(
                                        "http://localhost:8003/hosts", timeout=60
                                    ).json()
                                    if isinstance(hosts_list, dict) and "hosts" in hosts_list:
                                        hosts_list = hosts_list["hosts"]
                                    for h in hosts_list:
                                        if h.get("id") == host_id or h.get("ip") == host_id:
                                            ip = h.get("ip")
                                            break
                                except Exception:
                                    ip = None

                            if ip:
                                for wid, w in workers_by_id.items():
                                    if w.get("ip") == ip or w.get("host") == ip:
                                        worker_id = wid
                                        break

                        # 3) Fallback determinista si no se encontró
                        if worker_id is None:
                            worker_id = (abs(hash(host_id)) % len(workers_by_id)) + 1

                        # Registrar asignaciones
                        for vm_name in host_placement.get("assigned_vms", []):
                            vm_to_worker[vm_name] = worker_id
                            print(
                                f"[DeploymentManager] VM '{vm_name}' => Worker {worker_id} (from host_id={host_id})"
                            )
                else:
                    print("[DeploymentManager] WARNING: 'placements' no presente en respuesta")

            else:
                print(f"[DeploymentManager] WARNING: Placement API retornó {placement_response.status_code}")
                print("  Usando asignación round-robin como fallback")

        except requests.exceptions.RequestException as e:
            print(f"[DeploymentManager] WARNING: No se pudo conectar con Placement API: {e}")
            print("  Usando asignación round-robin como fallback")

        # ============================================
        # PASO 3: Desplegar las VMs
        # ============================================
        results = []

        for i, vm in enumerate(vms):
            print(f"\n[DeploymentManager] Desplegando VM: {vm['name']}")

            if vm["name"] in vm_to_worker:
                worker_id = vm_to_worker[vm["name"]]
                print(f"  ✓ Usando asignación del algoritmo I-GA: Worker {worker_id}")
            else:
                worker_id = (i % len(workers_by_id)) + 1
                print(f"  ⚠ Usando asignación round-robin: Worker {worker_id}")

            # Validar que el worker existe
            if worker_id not in workers_by_id:
                print(f"  ✗ Worker {worker_id} no existe. Usando Worker 1 como fallback")
                worker_id = 1

            # Verificar nuevamente después del fallback
            if worker_id not in workers_by_id:
                raise Exception(f"Worker {worker_id} no configurado en WORKERS")

            worker_port = workers_by_id[worker_id]["ssh_port"]
            vnc_port = (worker_id * 10000) + (slice_id % 100 * 100) + (vm["id"] % 100)

            image = db.execute(
                text("SELECT * FROM image WHERE id = :iid"),
                {"iid": vm["image_id"]}
            ).mappings().first()
            image_path = image["path"] if image else "/home/ubuntu/images/cirros-0.6.2-x86_64-disk.img"

            links = db.execute(
                text("""
                    SELECT vlan_id, vm_a, vm_b 
                    FROM network_link 
                    WHERE slice_id = :sid AND (vm_a = :vid OR vm_b = :vid)
                """),
                {"sid": slice_id, "vid": vm["id"]}
            ).mappings().all()
            vlans = [l["vlan_id"] for l in links]

            res = create_vm_multi_vlan(
                worker_port, vm["name"], "br-int", vlans, vnc_port,
                vm["cpu"], vm["ram"], vm["disk"], vm["num_interfaces"], image_path=image_path
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

            results.append({
                **res,
                "vm_name": vm["name"],
                "worker_id": worker_id,
                "vnc_port": vnc_port,
                "vlans": vlans
            })

        db.execute(
            text("UPDATE slice SET status = :status WHERE id = :sid"),
            {"status": "DESPLEGADO", "sid": slice_id}
        )
        db.commit()

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
