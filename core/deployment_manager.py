from core.db_config import SessionLocal
from core.models import Slice, VM, Image
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
            worker_id = i % len(worker_ips) + 1
            # Calculamos el puerto VNC basado en el ID del slice y la VM
            base_vnc = (worker_id * 10000) + (slice_id % 100 * 100) + (vm.id % 100)
            vnc_port = base_vnc

            # Obtener la ruta de la imagen desde la base de datos
            image = db.get(Image, vm.image_id)
            image_path = image.path if image else "/home/ubuntu/images/cirros-0.6.2-x86_64-disk.img"

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
    finally:
        db.close()

