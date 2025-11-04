from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from config.settings import WORKERS
from drivers.linux_drivers import create_vm_multi_vlan


# def generate_unique_name(db, model, base_name: str) -> str:
#     """
#     Genera un nombre único añadiendo un sufijo numérico si es necesario.
#     """
#     count = db.query(model).filter(
#         model.name.like(f"{base_name}%")
#     ).count()
#
#     if count == 0:
#         return base_name
#     return f"{base_name}-{count}"

def generate_unique_name(db, table_name: str, base_name: str) -> str:
    """
    Genera un nombre único añadiendo un sufijo numérico si es necesario.
    """
    # Validar tabla permitida
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
    Ahora recibe db como parámetro en lugar de crear SessionLocal()
    """
    try:
        # Obtener el slice
        slice_obj = db.execute(
            text("SELECT * FROM slice WHERE id = :sid"),
            {"sid": slice_id}
        ).mappings().first()

        if not slice_obj:
            raise HTTPException(status_code=404, detail="Slice no encontrado")

        # Generar nombre único para el slice si es necesario
        if slice_obj["status"] == "PENDIENTE":
            new_name = generate_unique_name(db, "slice", slice_obj["name"])  # Modificar esta función también
            db.execute(
                text("UPDATE slice SET name = :name WHERE id = :sid"),
                {"name": new_name, "sid": slice_id}
            )

        vms = db.execute(
            text("SELECT * FROM vm WHERE slice_id = :sid AND state = :state"),
            {"sid": slice_id, "state": "PENDIENTE"}
        ).mappings().all()

        if not vms:
            return "No hay VMs pendientes."

        db.execute(
            text("UPDATE slice SET status = :status WHERE id = :sid"),
            {"status": "DESPLEGANDO", "sid": slice_id}
        )
        db.commit()

        print("[DeploymentManager] Nombres únicos generados para Slice y VMs.")
        # Generar nombres únicos para todas las VMs pendientes
        for vm in vms:
            new_vm_name = generate_unique_name(db, "vm", vm["name"])  # Modificar función
            db.execute(
                text("UPDATE vm SET name = :name WHERE id = :vid"),
                {"name": new_vm_name, "vid": vm["id"]}
            )
        db.commit()

        results = []
        # worker_ips = WORKER_IPS
        workers = WORKERS
        # worker_ips = [worker["ip"] for worker in workers]
        print("[DeploymentManager] Workers disponibles:", workers)
        worker_ports = [worker["ssh_port"] for worker in workers.values()]
        print("[DeploymentManager] Worker SSH ports:", worker_ports)

        for i, vm in enumerate(vms):
            print("[DeploymentManager] Desplegando VM:", vm["name"])
            worker_port = worker_ports[i % len(worker_ports)]
            print(f"[DeploymentManager] VM {vm['name']} asignada al worker puerto {worker_port}")
            worker_id = i % len(worker_ports) + 1
            print(f"[DeploymentManager] Worker ID para VM {vm['name']}: {worker_id}")
            # Calculamos el puerto VNC basado en el ID del slice y la VM
            base_vnc = (worker_id * 10000) + (slice_id % 100 * 100) + (vm.id % 100)
            vnc_port = base_vnc

            # Obtener la ruta de la imagen desde la base de datos
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

            print(f"[DeploymentManager] Creando VM {vm['name']} en worker puerto {worker_port} con VLANs {vlans}")
            # Llamar a create_vm con múltiples VLANs
            res = create_vm_multi_vlan(
                worker_port,
                vm["name"],
                "br-int",
                vlans,  # Lista de VLANs en lugar de un solo VLAN
                vnc_port,
                vm["cpu"],
                vm["ram"],
                vm["disk"],
                vm["num_interfaces"],
                image_path=image_path
            )

            print(f"[DeploymentManager] Resultado de create_vm para {vm.name}: {res}")

            new_state = "DESPLEGADO" if res["success"] else "ERROR"
            vm_id = vm["id"]

            db.execute(
                text("UPDATE vm SET state = :state, worker_id = :worker_id WHERE id = :vid"),
                {"state": new_state, "worker_id": worker_id, "vid": vm_id}
            )

            if res["success"] and "pid" in res:
                pid_value = res["pid"]
                print(f"[DeploymentManager] Guardando PID {pid_value} para VM {vm['name']}")

                db.execute(
                    text("UPDATE vm SET pid = :pid WHERE id = :vid"),
                    {"pid": pid_value, "vid": vm_id}
                )
                db.flush()
            else:
                print(f"[DeploymentManager] No se pudo obtener PID para VM {vm['name']}")
                print(f"[DeploymentManager] Success: {res['success']}, PID en resultado: {'pid' in res}")

            results.append(res)
            print(f" SSH Tunnel: ssh -NL :30011:127.0.0.1:{vnc_port}")

        print("[DeploymentManager] Actualizando estado del slice a DESPLEGADO")
        db.execute(
            text("""
                UPDATE slice
                SET status = :status
                WHERE id = :sid
            """),
            {
                "status": "DESPLEGADO",
                "sid": slice_id
            }
        )
        db.commit()
        return results
    except Exception as e:
        print(f"Error creando slice: {e}")
        db.rollback()
        raise e

