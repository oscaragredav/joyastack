from pathlib import Path

from utils import ssh
from utils.ssh import SSHConnection
# --- Definiciones globales (o al inicio del archivo) ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_SCRIPT_NAME = "vm_create_multi.sh"
LOCAL_SCRIPT_PATH = PROJECT_ROOT / "scripts" / LOCAL_SCRIPT_NAME
REMOTE_SCRIPT_PATH = "/tmp/vm_create.sh"

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


def create_vm_multi_vlan(worker_port: int, vm_name: str, bridge: str, vlans: list,
                         vnc_port: int, cpus: int, ram_mb: int, disk_gb: int,
                         num_ifaces: int, image_path: str) -> dict:
    """
    Crea una VM con múltiples interfaces TAP, cada una en su propia VLAN.
    """

    conn = SSHConnection(port=worker_port)
    print(f"[LinuxDriver] Conectado al worker {worker_port}")
    conn.connect()
    print(f"[LinuxDriver] Conexión establecida con el worker {worker_port}")

    # --- 1. Lógica de transferencia SFTP ---
    try:
        sftp = conn.client.open_sftp()
        print(f"[LinuxDriver] Transfiriendo script local desde: {LOCAL_SCRIPT_PATH}")

        # Sube el archivo
        sftp.put(LOCAL_SCRIPT_PATH.as_posix(), REMOTE_SCRIPT_PATH)
        sftp.chmod(REMOTE_SCRIPT_PATH, 0o755)
        sftp.close()

        # --- 2. 🟢 CORRECCIÓN CRLF APLICADA ---
        # Ejecutar 'sed' o 'dos2unix' para eliminar los caracteres '\r' (CR)
        print(f"[LinuxDriver] Corrigiendo fin de línea (CRLF a LF) en {REMOTE_SCRIPT_PATH}...")
        correction_cmd = f"sed -i 's/\\r$//' {REMOTE_SCRIPT_PATH}"

        # Ejecutar el comando de corrección
        # Usamos exec_command del cliente paramiko directamente para evitar el wrapper exec_sudo si es posible,
        # pero para ser coherentes, lo mantenemos como exec_sudo si el user ubuntu requiere permisos.
        _, correction_stdout, correction_stderr = conn.client.exec_command(correction_cmd)

        # NOTA: En un entorno de producción, la corrección DEBERÍA ejecutarse con sudo
        # si el REMOTE_SCRIPT_PATH no es escribible por el usuario ssh.
        # Si conn.exec_sudo se usa, asegúrate de que maneje el STDOUT/STDERR correctamente.
        # Asumiendo que /tmp/ es escribible, podemos usar client.exec_command para comandos simples.

        if correction_stderr.read():
            print(f"[LinuxDriver] Aviso: Error menor al corregir CRLF: {correction_stderr.read().decode()}")

    except Exception as e:
        # Manejo de errores de transferencia o corrección
        print(f"[LinuxDriver] ERROR en transferencia/corrección: {e}")
        return {
            "worker_port": worker_port,
            "vm_name": vm_name,
            "stdout": "",
            "stderr": f"Error de transferencia/corrección de script: {e}",
            "success": False,
            "pid": None,
            "vlans": vlans
        }

    # --- 3. Ejecución del Script ---
    # Convertir lista de VLANs a string separado por comas
    vlans_str = ",".join(map(str, vlans)) if vlans else "0"

    # Usamos REMOTE_SCRIPT_PATH para la ejecución
    cmd = f"{REMOTE_SCRIPT_PATH} {vm_name} {bridge} '{vlans_str}' {int(vnc_port)} {int(cpus)} {int(ram_mb)} {int(disk_gb)} {int(num_ifaces)} {image_path}"

    print(f"[LinuxDriver] Creando {vm_name} con VLANs: {vlans}")

    try:
        print(f"[LinuxDriver] Ejecutando comando: {cmd}")
        stdout, stderr = conn.exec_sudo(cmd)
        print(f"[LinuxDriver] STDOUT:\n{stdout}")
        print(f"[LinuxDriver] STDERR:\n{stderr}")

        # ... (El resto de la lógica de extracción de PID, success, y return) ...
        # (Se mantiene tu lógica original de extracción de PID)

        pid = None
        for line in stdout.split('\n'):
            if 'PID' in line:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        # Asumiendo que el PID es el último elemento entre paréntesis o solo el último
                        pid_str = parts[-1].strip('()')
                        pid = int(pid_str)
                    except ValueError:
                        pass
        print(f"[LinuxDriver] PID extraído: {pid}")

        success = stderr == "" and "creada correctamente" in stdout

        return {
            "worker_port": worker_port,
            "vm_name": vm_name,
            "stdout": stdout,
            "stderr": stderr,
            "success": success,
            "pid": pid,
            "vlans": vlans
        }

    finally:
        conn.close()


def delete_vm_resources(vm, wip: str, worker_port: int, slice_id=None) -> bool:
    """
    Detiene el proceso QEMU y elimina los puertos OvS y las interfaces TAP
    asociadas a una máquina virtual específica en el worker.

    Returns:
        bool: True si la limpieza tuvo éxito, False en caso contrario.
    """

    conn = SSHConnection(port=worker_port)
    print(f"[LinuxDriver] Conectado al worker {worker_port}")
    conn.connect()
    print(f"[LinuxDriver] Conexión establecida con el worker {worker_port}")

    # Si la conexión se establece:
    try:
        vm_name = vm['name'].split('-')[0]
        print(f"[SliceManager] Eliminando VM {vm_name} en worker {wip}")

        # --- 2. Matar proceso QEMU ---
        # El uso de comillas simples internas ('') es la corrección de sintaxis aplicada
        kill_cmd = f"pkill -f 'qemu-system.*{vm_name}' || true"
        print(f"[SliceManager] Ejecutando: {kill_cmd}")
        conn.exec_sudo(kill_cmd)

        # Esperar un poco para asegurar que el sistema operativo libere los recursos TAP
        conn.exec_sudo(f"sleep 1")

        # --- 3. Limpiar TAPs de OvS (Puertos) ---
        ovs_del_cmd = (
            f"ovs-vsctl list-ports br-int | grep {vm_name} | xargs -r -I{{}} ovs-vsctl del-port br-int {{}}"
        )
        print(f"[SliceManager] Ejecutando: {ovs_del_cmd}")
        conn.exec_sudo(ovs_del_cmd)

        # --- 4. Limpiar Interfaces TAP del SO (ip link) ---
        ip_del_cmd = (
            f"ip link del $(ip link show | grep {vm_name} | cut -d: -f2) 2>/dev/null || true"
        )
        print(f"[SliceManager] Ejecutando: {ip_del_cmd}")
        conn.exec_sudo(ip_del_cmd)

        print(f"[SliceManager] Recursos de VM {vm_name} limpiados correctamente.")
        return True

    except Exception as e:
        print(f"Error limpiando recursos de VM: {e}")
        print(f"[SliceManager] ERROR limpiando recursos de VM en worker {wip}")
        return False

    finally:
        conn.close()
        print(f"[SliceManager] Conexión cerrada.")