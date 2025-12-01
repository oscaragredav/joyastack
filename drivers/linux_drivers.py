from utils import ssh
from utils.ssh import SSHConnection
from pathlib import Path
import time
import re


# La función create_vm original del usuario (para contexto, no se está usando el image_path en el cmd)
def create_vm(worker_ip: str, vm_name: str, bridge: str, vlan: int,
              vnc_port: int, cpus: int, ram_mb: int, disk_gb: int,
              num_ifaces: int = 1, image_path: str = "/home/ubuntu/images/cirros-0.6.2-x86_64-disk.img") -> dict:
    script_path = "/home/ubuntu/joyastack/scripts/vm_create.sh"
    # Asegurar que todos los argumentos sean enteros donde corresponde
    # NOTA: Este cmd solo envía 8 argumentos, el script remoto espera 9 si es la versión multi-vlan/sudo.
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


def create_vm_multi_vlan(worker_port: int, vm_name: str, bridge: str, vlans: list,
                         vnc_port: int, cpus: int, ram_mb: int, disk_gb: int,
                         num_ifaces: int, image_path: str) -> dict:
    """
    Crea una VM con múltiples interfaces TAP, cada una en su propia VLAN.

    CORRECCIÓN ROBUSTA: Asegura la subida del script por SFTP y usa la lectura de archivos
    persistentes (VM1_info.txt) para obtener el PID de forma confiable.
    """

    conn = SSHConnection(port=worker_port)
    print(f"[LinuxDriver] Conectado al worker {worker_port}")
    conn.connect()
    print(f"[LinuxDriver] Conexión establecida con el worker {worker_port}")

    script_path = "/tmp/vm_create.sh"
    info_file_path = f"/home/ubuntu/joyastack/var/vms/{vm_name}_info.txt"

    # --- 1. Subir el script (Lógica corregida: Sube el script a /tmp/vm_create.sh) ---
    local_script_name = "vm_create.sh"
    # Busca el script asumiendo la estructura de carpetas (scripts/vm_create.sh)
    local_script = Path(__file__).resolve().parent.parent / "scripts" / local_script_name

    if not local_script.exists():
        # Fallback de ruta, por si la estructura de carpetas es diferente (e.g., está en el mismo cwd)
        local_script = Path.cwd() / "scripts" / local_script_name
        if not local_script.exists():
            print(
                f"[LinuxDriver] ERROR: Script local no encontrado. Buscado en: {Path(__file__).resolve().parent.parent / 'scripts'} y {Path.cwd() / 'scripts'}")
            return {
                "worker_port": worker_port,
                "vm_name": vm_name,
                "stdout": "",
                "stderr": "Local script vm_create.sh not found.",
                "success": False,
                "pid": None,
                "vlans": vlans,
                "metadata_output": ""
            }

    try:
        # CORRECCIÓN CRLF: Leer el contenido del archivo, limpiar \r, y escribirlo en el destino remoto

        # Leer el contenido del script local
        with open(local_script, 'r', encoding='utf-8') as f:
            script_content = f.read()

        # Limpiar caracteres de retorno de carro (\r) para asegurar que sea formato LF (Unix)
        cleaned_content = script_content.replace('\r\n', '\n').replace('\r', '\n')

        sftp = conn.client.open_sftp()
        # Escribir el contenido limpio al path de ejecución /tmp/vm_create.sh
        with sftp.open(script_path, 'w') as remote_file:
            remote_file.write(cleaned_content)

        sftp.chmod(script_path, 0o755)
        sftp.close()
        print(f"[LinuxDriver] Script {local_script_name} subido y permisos establecidos en {script_path}.")
        print(f"[LinuxDriver] Aviso: Se corrigió la codificación de fin de línea (CRLF a LF) durante la subida.")
    except Exception as e:
        print(f"[LinuxDriver] ERROR al subir el script via SFTP: {e}")
        conn.close()
        return {
            "worker_port": worker_port,
            "vm_name": vm_name,
            "stdout": "",
            "stderr": f"SFTP upload failed: {e}",
            "success": False,
            "pid": None,
            "vlans": vlans,
            "metadata_output": ""
        }

    # Convertir lista de VLANs a string separado por comas
    vlans_str = ",".join(map(str, vlans)) if vlans else "0"

    # Comando completo (9 argumentos, con image_path incluido)
    # CORRECCIÓN: Anteponemos 'bash ' para forzar la ejecución del script, evitando el error "No such file or directory"
    cmd = f"bash {script_path} {vm_name} {bridge} '{vlans_str}' {int(vnc_port)} {int(cpus)} {int(ram_mb)} {int(disk_gb)} {int(num_ifaces)} {image_path}"

    print(f"[LinuxDriver] Creando {vm_name} con VLANs: {vlans}")

    pid = None
    success = False

    try:
        print(f"[LinuxDriver] Ejecutando comando: {cmd}")
        # La ejecución retorna casi inmediatamente debido a 'nohup &'.
        stdout, stderr = conn.exec_sudo(cmd)

        # STDOUT/STDERR ahora contienen los logs de ejecución del script (Fix V9)
        print(f"[LinuxDriver] Logs del script remoto (STDOUT/STDERR):\n{stdout}\n{stderr}")

        # --- 2. Esperar y leer el archivo de metadatos (Fuente de verdad) ---
        # Usamos un pequeño delay para asegurar que el I/O en el host termine.
        time.sleep(1)

        read_cmd = f"cat {info_file_path}"
        info_stdout, info_stderr = conn.exec_sudo(read_cmd)

        if info_stderr and "[sudo] password for ubuntu:" not in info_stderr:
            # Ignoramos la solicitud de password de sudo, pero cualquier otro error es grave
            print(f"[LinuxDriver] ERROR al leer info file: {info_stderr}")
            success = False
        else:
            # Parsear el archivo de metadatos
            metadata = {}
            for line in info_stdout.split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    metadata[key.strip()] = value.strip()

            pid = metadata.get('PID')

            if pid and pid.isdigit():
                pid = int(pid)
                success = True
                print(f"[LinuxDriver] Metadata File read successfully. PID found: {pid}")
            else:
                # Si el PID no se encuentra, la VM falló en su creación.
                print(f"[LinuxDriver] WARNING: Could not find valid PID in metadata file.")
                success = False

        return {
            "worker_port": worker_port,
            "vm_name": vm_name,
            "stdout": stdout,  # Logs del script
            "stderr": stderr,  # Logs del script
            "success": success,
            "pid": pid,
            "vlans": vlans,
            "metadata_output": info_stdout.strip()
        }
    finally:
        conn.close()