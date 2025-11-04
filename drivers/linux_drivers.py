from utils.ssh import SSHConnection


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


def create_vm_multi_vlan(worker_port: str, vm_name: str, bridge: str, vlans: list,
                         vnc_port: int, cpus: int, ram_mb: int, disk_gb: int,
                         num_ifaces: int, image_path: str) -> dict:
    """
    Crea una VM con múltiples interfaces TAP, cada una en su propia VLAN.
    """
    script_path = "/home/ubuntu/joyastack/scripts/vm_create_multi.sh"

    # Convertir lista de VLANs a string separado por comas
    vlans_str = ",".join(map(str, vlans)) if vlans else "0"

    cmd = f"{script_path} {vm_name} {bridge} '{vlans_str}' {int(vnc_port)} {int(cpus)} {int(ram_mb)} {int(disk_gb)} {int(num_ifaces)} {image_path}"

    print(f"[LinuxDriver] Creando {vm_name} con VLANs: {vlans}")

    conn = SSHConnection(port=worker_port)
    conn.connect()
    print(f"[LinuxDriver] Conectado al worker {worker_port}")

    try:
        stdout, stderr = conn.exec_sudo(cmd)

        # Extraer PID
        pid = None
        for line in stdout.split('\n'):
            if 'PID' in line:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        pid = int(parts[-1].strip('()'))
                    except ValueError:
                        pass

        success = stderr == "" or "creada correctamente" in stdout

        return {
            "worker_ip": worker_ip,
            "vm_name": vm_name,
            "stdout": stdout,
            "stderr": stderr,
            "success": success,
            "pid": pid,
            "vlans": vlans
        }
    finally:
        conn.close()
