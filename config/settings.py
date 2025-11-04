import os

from dotenv import load_dotenv

load_dotenv()

# --- Configuración de base de datos ---
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASS = os.getenv("MYSQL_PASSWORD", "root")
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("MYSQL_DB", "joyastack")

DB_URL = f"mysql+mysqlconnector://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# --- Otros parámetros globales para añadir al .env ---
# WORKER_IPS = ["192.168.201.2", "192.168.201.3", "192.168.201.4"]
WORKERS = {
    "worker1": {"ip": "192.168.201.2", "ssh_port": 5811},
    "worker2": {"ip": "192.168.201.3", "ssh_port": 5812},
    "worker3": {"ip": "192.168.201.4", "ssh_port": 5813},
}
HEADNODE = {"ip": "192.168.201.1", "ssh_port": 5814}
SSH_USER = "ubuntu"
GATEWAY = "10.20.12.154"
SSH_PASS = "RedesCloud2025"
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", "/home/ubuntu/.ssh/id_rsa")
