from utils.ssh_utils import SSHConnection

def create_vm(worker_ip: str, vm_name: str, bridge: str, vlan: int,
              vnc_port: int, cpus: int, ram_mb: int, disk_gb: int,
              num_ifaces: int = 1, image_path: str = "/home/ubuntu/images/cirros-0.6.2-x86_64-disk.img") -> dict:
    script_path = "/home/ubuntu/joyastack/scripts/vm_create.sh"
    # Asegurar que todos los argumentos sean enteros donde corresponde
    cmd = f"{script_path} {vm_name} {bridge} {int(vlan)} {int(vnc_port)} {int(cpus)} {int(ram_mb)} {int(disk_gb)} {int(num_ifaces)}"

    print(f"[LinuxDriver] Conectando con {worker_ip} para crear {vm_name}...")

    conn = SSHConnection(worker_ip)
    conn.connect()

    try:
        # Verificar si todos los argumentos están presentes
        args = [vm_name, bridge, vlan, vnc_port, cpus, ram_mb, disk_gb, num_ifaces]
        if any(arg is None for arg in args):
            print(f"[LinuxDriver] ERROR: Faltan argumentos requeridos")
            return {
                "worker_ip": worker_ip,
                "vm_name": vm_name,
                "stdout": "ERROR: Faltan argumentos requeridos",
                "stderr": "",
                "success": False,
                "pid": None
            }

        stdout, stderr = conn.exec_sudo(cmd)
        print(f"[LinuxDriver] STDOUT:\n{stdout}")
        if stderr:
            print(f"[LinuxDriver] STDERR:\n{stderr}")

        # Verificar si hay mensaje de error en la salida
        if "ERROR:" in stdout:
            print(f"[LinuxDriver] Se detectó un error en la creación de la VM")
            return {
                "worker_ip": worker_ip,
                "vm_name": vm_name,
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
                "success": False,
                "pid": None
            }

        success = "creada" in stdout.lower() or "vm" in stdout.lower()
            
        # Obtener el PID de la VM recién creada solo si fue exitosa
        vm_pid = None
        if success:
            pid_cmd = f"ps aux | grep '[q]emu-system-x86_64.*-name {vm_name} ' | awk '{{print $2}}'"
            pid_stdout, pid_stderr = conn.exec_sudo(pid_cmd)
            vm_pid = int(pid_stdout.strip()) if pid_stdout.strip() else None
            print(f"[LinuxDriver] PID command: {pid_cmd}")
            print(f"[LinuxDriver] PID stdout: {pid_stdout}")
            print(f"[LinuxDriver] PID found: {vm_pid}")
        
        return {
            "worker_ip": worker_ip,
            "vm_name": vm_name,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
            "success": success,
            "pid": vm_pid
        }
    finally:
        conn.close()
