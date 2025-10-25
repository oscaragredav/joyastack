import paramiko
import os
from typing import Tuple

def run(host: str, cmd: str, user: str = "ubuntu", port: int = 22, timeout: int = 30) -> Tuple[int, str, str]:
    """
    Ejecuta un comando remoto vía SSH.
    Retorna (exit_code, stdout, stderr)
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(hostname=host, username=user, port=port, timeout=timeout)
        stdin, stdout, stderr = ssh.exec_command(cmd)
        out = stdout.read().decode()
        err = stderr.read().decode()
        rc = stdout.channel.recv_exit_status()
        return rc, out.strip(), err.strip()
    finally:
        ssh.close()

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
