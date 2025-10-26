from core.db_config import SessionLocal
from core.models import Slice, VM
from drivers.linux_drivers import create_vm
from sqlalchemy import func

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

def deploy_slice(slice_id: int):
    db = SessionLocal()
    try:
        slice_obj = db.get(Slice, slice_id)
        # Generar nombre único para el slice si es necesario
        if slice_obj.status == "PENDIENTE":
            slice_obj.name = generate_unique_name(db, Slice, slice_obj.name)
            
        vms = db.query(VM).filter(VM.slice_id == slice_id, VM.state == "PENDIENTE").all()
        if not vms:
            return "No hay VMs pendientes."

        slice_obj.status = "DESPLEGANDO"
        db.commit()

        # Generar nombres únicos para todas las VMs pendientes
        for vm in vms:
            vm.name = generate_unique_name(db, VM, vm.name)
        db.commit()

        results = []
        worker_ips = ["10.0.10.2", "10.0.10.3", "10.0.10.4"]

        for i, vm in enumerate(vms):
            worker_ip = worker_ips[i % len(worker_ips)]
            res = create_vm(worker_ip, vm.name, "br-int", 0, 10 + i, vm.cpu, vm.ram, vm.disk)
            vm.state = "DESPLEGADO" if res["success"] else "ERROR"
            vm.worker_id = i % len(worker_ips) + 1
            results.append(res)

        slice_obj.status = "DESPLEGADO"
        db.commit()
        return results
    finally:
        db.close()

