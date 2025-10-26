from core.db_config import SessionLocal
from core.models import Slice, VM
from drivers.linux_drivers import create_vm

def deploy_slice(slice_id: int):
    db = SessionLocal()
    try:
        slice_obj = db.get(Slice, slice_id)
        vms = db.query(VM).filter(VM.slice_id == slice_id, VM.state == "PENDIENTE").all()
        if not vms:
            return "No hay VMs pendientes."

        slice_obj.status = "DESPLEGANDO"
        db.commit()

        results = []
        worker_ips = ["10.0.10.2", "10.0.10.3", "10.0.10.4"]

        for i, vm in enumerate(vms):
            worker_id = i % len(worker_ips) + 1
            worker_ip = worker_ips[i % len(worker_ips)]
            
            # Calculamos el puerto VNC basado en el ID del slice y la VM
            # Formato: WXXYY donde W es el ID del worker (1-9), XX es el ID del slice (00-99) y YY es el ID de la VM (00-99)
            base_vnc = (worker_id * 10000) + (slice_id % 100 * 100) + (vm.id % 100)
            vnc_port = base_vnc
            
            res = create_vm(worker_ip, vm.name, "br-int", 0, vnc_port, vm.cpu, vm.ram, vm.disk)
            vm.state = "DESPLEGADO" if res["success"] else "ERROR"
            vm.worker_id = worker_id
            if res["success"] and res.get("pid"):
                vm.pid = res["pid"]
            results.append(res)

        slice_obj.status = "DESPLEGADO"
        db.commit()
        return results
    finally:
        db.close()

