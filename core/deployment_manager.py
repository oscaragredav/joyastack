from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from config.settings import WORKER_IPS
from drivers.linux_drivers import create_vm


def generate_unique_name(db, model, base_name: str) -> str:
    """
    Genera un nombre único añadiendo un sufijo numérico si es necesario.
    """
    count = db.query(model).filter(
        model.name.like(f"{base_name}%")
    ).count()

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
            new_name = generate_unique_name(db, slice_obj["name"])  # Modificar esta función también
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

        # Generar nombres únicos para todas las VMs pendientes
        for vm in vms:
            new_vm_name = generate_unique_name(db, vm["name"])  # Modificar función
            db.execute(
                text("UPDATE vm SET name = :name WHERE id = :vid"),
                {"name": new_vm_name, "vid": vm["id"]}
            )
        db.commit()

        results = []
        worker_ips = WORKER_IPS

        for i, vm in enumerate(vms):
            worker_ip = worker_ips[i % len(worker_ips)]
            worker_id = i % len(worker_ips) + 1
            # Calculamos el puerto VNC basado en el ID del slice y la VM
            base_vnc = (worker_id * 10000) + (slice_id % 100 * 100) + (vm.id % 100)
            vnc_port = base_vnc

            # Obtener la ruta de la imagen desde la base de datos
            image = db.execute(
                text("SELECT * FROM image WHERE id = :iid"),
                {"iid": vm["image_id"]}
            ).mappings().first()

            image_path = image["path"] if image else "/home/ubuntu/images/cirros-0.6.2-x86_64-disk.img"

            res = create_vm(
                worker_ip,
                vm.name,
                "br-int",
                0,
                vnc_port,
                vm.cpu,
                vm.ram,
                vm.disk,
                1,
                image_path=image_path)

            print(f"[DeploymentManager] Resultado de create_vm para {vm.name}: {res}")

            vm.state = "DESPLEGADO" if res["success"] else "ERROR"
            vm.worker_id = worker_id

            if res["success"] and "pid" in res:
                print(f"[DeploymentManager] Guardando PID {res['pid']} para VM {vm.name}")
                vm.pid = res["pid"]
                db.add(vm)
                db.flush()
            else:
                print(f"[DeploymentManager] No se pudo obtener PID para VM {vm.name}")
                print(f"[DeploymentManager] Success: {res['success']}, PID en resultado: {'pid' in res}")

            results.append(res)

        slice_obj.status = "DESPLEGADO"
        db.commit()
        return results
    except Exception as e:
        db.rollback()
        raise e

