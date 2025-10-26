from utils.ssh_utils import SSHConnection

def create_vm(worker_ip: str, vm_name: str, bridge: str, vlan: int,
              vnc_port: int, cpus: int, ram_mb: int, disk_gb: int,
              num_ifaces: int = 1, image_name: str = "cirros-0.6.2-x86_64-disk.img") -> dict:
    """
    Crea una VM en el worker remoto ejecutando el script vm_create.sh
    Retorna un diccionario con el estado de la ejecución.
    """
    script_path = "/home/ubuntu/joyastack/scripts/vm_create.sh"
    cmd = f"{script_path} {vm_name} {bridge} {vlan} {vnc_port} {cpus} {ram_mb} {disk_gb} {num_ifaces}"

    print(f"[LinuxDriver] Conectando con {worker_ip} para crear {vm_name}...")

    conn = SSHConnection(worker_ip)
    conn.connect()

    try:
        stdout, stderr = conn.exec_sudo(cmd)
        success = "creada" in stdout.lower() or "vm" in stdout.lower()
        print(f"[LinuxDriver] STDOUT:\n{stdout}")
        if stderr:
            print(f"[LinuxDriver] STDERR:\n{stderr}")
            
        # Obtener el PID de la VM recién creada
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
