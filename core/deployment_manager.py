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


def deploy_slice(slice_id: int, db: Session):
    """
    Despliega un slice utilizando el algoritmo genético de placement para
    asignar VMs a workers de forma óptima.

    1. Genera nombres únicos para slice y VMs
    2. Obtiene placement óptimo desde la API (I-GA)
    3. Despliega cada VM en el worker asignado por el algoritmo
    4. Configura VLANs y networking
    """
    try:
        # ============================================
        # PASO 1: Validación y preparación del slice
        # ============================================
        slice_obj = db.execute(
            text("SELECT * FROM slice WHERE id = :sid"),
            {"sid": slice_id}
        ).mappings().first()

        if not slice_obj:
            raise HTTPException(status_code=404, detail="Slice no encontrado")

        # Generar nombre único para el slice si es necesario
        if slice_obj["status"] == "PENDIENTE":
            new_name = generate_unique_name(db, "slice", slice_obj["name"])
            db.execute(
                text("UPDATE slice SET name = :name WHERE id = :sid"),
                {"name": new_name, "sid": slice_id}
            )

        # Obtener VMs pendientes
        vms = db.execute(
            text("SELECT * FROM vm WHERE slice_id = :sid AND state = :state"),
            {"sid": slice_id, "state": "PENDIENTE"}
        ).mappings().all()

        if not vms:
            return {"message": "No hay VMs pendientes.", "results": []}

        # Actualizar estado del slice a DESPLEGANDO
        db.execute(
            text("UPDATE slice SET status = :status WHERE id = :sid"),
            {"status": "DESPLEGANDO", "sid": slice_id}
        )
        db.commit()

        print("[DeploymentManager] Nombres únicos generados para Slice y VMs.")

        # Generar nombres únicos para todas las VMs pendientes
        for vm in vms:
            new_vm_name = generate_unique_name(db, "vm", vm["name"])
            db.execute(
                text("UPDATE vm SET name = :name WHERE id = :vid"),
                {"name": new_vm_name, "vid": vm["id"]}
            )
        db.commit()

        # ============================================
        # PASO 2: Obtener placement óptimo desde API
        # ============================================
        placement_data = None
        vm_to_worker = {}  # Mapeo de vm_name -> worker_id

        try:
            print("[DeploymentManager] Solicitando placement óptimo al algoritmo I-GA...")
            placement_response = requests.post(
                f"http://localhost:8002/placement/slice/{slice_id}",
                timeout=30
            )

            if placement_response.status_code == 200:
                placement_data = placement_response.json()
                print(f"[DeploymentManager] Placement obtenido con éxito:")
                print(f"  - Energía total: {placement_data['total_energy']} W")
                print(f"  - Disponibilidad: {placement_data['total_availability']}")
                print(f"  - Fitness score: {placement_data['fitness_score']}")

                # Crear mapeo de VM a Worker desde el placement
                for host_placement in placement_data["placements"]:
                    host_id = host_placement["host_id"]  # e.g., "host1", "host2"

                    # Extraer el número del host_id (host1 -> 1, host2 -> 2)
                    try:
                        worker_id = int(host_id.replace("host", ""))
                    except ValueError:
                        # Si el formato no es "hostN", usar el host_id completo
                        worker_id = hash(host_id) % len(WORKERS) + 1

                    for vm_name in host_placement["assigned_vms"]:
                        vm_to_worker[vm_name] = worker_id
                        print(f"  - VM '{vm_name}' asignada a Worker {worker_id}")
            else:
                print(f"[DeploymentManager] WARNING: Placement API retornó {placement_response.status_code}")
                print(f"  Usando asignación round-robin como fallback")

        except requests.exceptions.RequestException as e:
            print(f"[DeploymentManager] WARNING: No se pudo conectar con Placement API: {e}")
            print(f"  Usando asignación round-robin como fallback")
        except Exception as e:
            print(f"[DeploymentManager] WARNING: Error procesando placement: {e}")
            print(f"  Usando asignación round-robin como fallback")

        # ============================================
        # PASO 3: Desplegar VMs en los workers
        # ============================================
        results = []
        workers = WORKERS
        print("[DeploymentManager] Workers disponibles:", workers)
        worker_ports = [worker["ssh_port"] for worker in workers.values()]
        print("[DeploymentManager] Worker SSH ports:", worker_ports)

        for i, vm in enumerate(vms):
            print(f"\n[DeploymentManager] Desplegando VM: {vm['name']}")

            # Determinar worker_id usando placement o fallback a round-robin
            if vm["name"] in vm_to_worker:
                worker_id = vm_to_worker[vm["name"]]
                print(f"  ✓ Usando asignación del algoritmo I-GA: Worker {worker_id}")
            else:
                worker_id = (i % len(WORKERS)) + 1
                print(f"  ⚠ Usando asignación round-robin (fallback): Worker {worker_id}")

            # Validar que el worker_id existe
            if worker_id not in workers:
                print(f"  ✗ ERROR: Worker {worker_id} no existe. Usando Worker 1 como fallback")
                worker_id = 1

            worker_port = workers[worker_id]["ssh_port"]
            print(f"  → Puerto SSH: {worker_port}")

            # Calcular puerto VNC
            base_vnc = (worker_id * 10000) + (slice_id % 100 * 100) + (vm["id"] % 100)
            vnc_port = base_vnc
            print(f"  → Puerto VNC: {vnc_port}")

            # Obtener la ruta de la imagen
            image = db.execute(
                text("SELECT * FROM image WHERE id = :iid"),
                {"iid": vm["image_id"]}
            ).mappings().first()

            image_path = image["path"] if image else "/home/ubuntu/images/cirros-0.6.2-x86_64-disk.img"

            # Obtener los links donde esta VM participa
            links = db.execute(
                text("""
                    SELECT vlan_id, vm_a, vm_b 
                    FROM network_link 
                    WHERE slice_id = :sid AND (vm_a = :vid OR vm_b = :vid)
                """),
                {"sid": slice_id, "vid": vm["id"]}
            ).mappings().all()

            # Preparar lista de VLANs para esta VM
            vlans = [link["vlan_id"] for link in links]
            print(f"  → VLANs configuradas: {vlans}")

            # Crear la VM con múltiples VLANs
            res = create_vm_multi_vlan(
                worker_port,
                vm["name"],
                "br-int",
                vlans,
                vnc_port,
                vm["cpu"],
                vm["ram"],
                vm["disk"],
                vm["num_interfaces"],
                image_path=image_path
            )

            print(f"  → Resultado: {res}")

            # Actualizar estado de la VM
            new_state = "DESPLEGADO" if res["success"] else "ERROR"
            vm_id = vm["id"]

            db.execute(
                text("UPDATE vm SET state = :state, worker_id = :worker_id WHERE id = :vid"),
                {"state": new_state, "worker_id": worker_id, "vid": vm_id}
            )

            # Guardar PID si está disponible
            if res["success"] and "pid" in res:
                pid_value = res["pid"]
                print(f"  ✓ Guardando PID {pid_value} para VM {vm['name']}")
                db.execute(
                    text("UPDATE vm SET pid = :pid WHERE id = :vid"),
                    {"pid": pid_value, "vid": vm_id}
                )
                db.flush()
            else:
                print(f"  ⚠ No se pudo obtener PID para VM {vm['name']}")

            results.append({
                **res,
                "vm_name": vm["name"],
                "worker_id": worker_id,
                "vnc_port": vnc_port,
                "vlans": vlans
            })

            print(f"  → SSH Tunnel: ssh -NL :30011:127.0.0.1:{vnc_port}")

        # ============================================
        # PASO 4: Finalizar despliegue
        # ============================================
        print("\n[DeploymentManager] Actualizando estado del slice a DESPLEGADO")
        db.execute(
            text("UPDATE slice SET status = :status WHERE id = :sid"),
            {"status": "DESPLEGADO", "sid": slice_id}
        )
        db.commit()

        # Preparar respuesta con información del placement
        response = {
            "slice_id": slice_id,
            "slice_name": slice_obj["name"],
            "vms_deployed": len(results),
            "results": results
        }

        # Agregar métricas del placement si está disponible
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