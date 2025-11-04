import paramiko
import os

class SSHConnection:
    def __init__(self, host, user="ubuntu", password="RedesCloud2025", port=22, timeout=30):
        self.host = host
        self.user = user
        self.port = port
        self.password = password
        self.timeout = timeout
        self.client = None

    def connect(self):
        """Establece conexión SSH"""
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            hostname=self.host,
            username=self.user,
            port=self.port,
            timeout=self.timeout
        )

    def close(self):
        """Cierra la conexión SSH"""
        if self.client:
            self.client.close()
            self.client = None

    def exec_command(self, command):
        """Ejecuta un comando normal"""
        if not self.client:
            raise Exception("No hay conexión SSH activa")
        stdin, stdout, stderr = self.client.exec_command(command)
        return stdout.read().decode(), stderr.read().decode()

    def exec_sudo(self, command):
        """Ejecuta un comando con sudo enviando la contraseña (no seguro, solo para pruebas)"""
        if not self.client:
            raise Exception("No hay conexión SSH activa")
        cmd = f"sudo -S {command}"
        stdin, stdout, stderr = self.client.exec_command(cmd)
        stdin.write("ubuntu\n")
        stdin.flush()
        return stdout.read().decode(), stderr.read().decode()


def run(host: str, cmd: str, user: str = "ubuntu", port: int = 22, timeout: int = 30):
    """
    Ejecución rápida de un comando SSH (sin clase persistente)
    """
    conn = SSHConnection(host, user, port, timeout)
    conn.connect()
    out, err = conn.exec_sudo(cmd) if cmd.startswith("sudo ") else conn.exec_command(cmd)
    conn.close()
    return 0 if not err else 1, out.strip(), err.strip()


def push(host: str, src: str, dst: str, user: str = "ubuntu", port: int = 22, timeout: int = 30):
    """
    Copia un archivo local a un destino remoto vía SFTP.
    """
    transport = paramiko.Transport((host, port))
    transport.connect(username=user)
    sftp = paramiko.SFTPClient.from_transport(transport)
    try:
        remote_dir = os.path.dirname(dst)
        try:
            sftp.stat(remote_dir)
        except IOError:
            sftp.mkdir(remote_dir)
        sftp.put(src, dst)
    finally:
        sftp.close()
        transport.close()
