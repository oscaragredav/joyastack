import os
from utils.ssh_utils import run

def create_vm(worker_ip: str, vm_name: str, bridge: str, vlan: int,
              vnc_port: int, cpus: int, ram_mb: int, disk_gb: int, num_ifaces: int = 1,
              image_name: str = "cirros-0.6.2-x86_64-disk.img") -> dict:
    """
    Crea una VM en el worker remoto ejecutando el script vm_create.sh
    Retorna un diccionario con el estado de la ejecución.
    """
    script_path = "/home/ubuntu/joyastack/scripts/vm_create.sh"

    cmd = f"sudo {script_path} {vm_name} {bridge} {vlan} {vnc_port} {cpus} {ram_mb} {disk_gb} {num_ifaces}"

    print(f"[LinuxDriver] Ejecutando en {worker_ip}: {cmd}")

    rc, out, err = run(worker_ip, cmd)

    result = {
        "worker_ip": worker_ip,
        "vm_name": vm_name,
        "return_code": rc,
        "stdout": out,
        "stderr": err,
        "success": rc == 0
    }

    if rc == 0:
        print(f"[LinuxDriver] VM {vm_name} creada exitosamente en {worker_ip}")
    else:
        print(f"[LinuxDriver] Error al crear VM {vm_name} en {worker_ip}: {err}")

    return result
