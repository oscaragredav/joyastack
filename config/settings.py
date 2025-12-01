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
WORKER_IPS = ["192.168.201.2", "192.168.201.3", "192.168.201.4"]
WORKERS = {
    "worker1": {"ip": "192.168.201.1", "ssh_port": 5811},
    "worker2": {"ip": "192.168.201.2", "ssh_port": 5812},
    "worker3": {"ip": "192.168.201.3", "ssh_port": 5813},
    "worker4": {"ip": "192.168.201.4", "ssh_port": 5814},

}
HEADNODE = {"ip": "192.168.201.1", "ssh_port": 5811}
SSH_USER = "ubuntu"
GATEWAY = "10.20.12.154"
SSH_PASS = "RedesCloud2025"
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", "/home/ubuntu/.ssh/id_rsa")

# --- OPENSTACK CONFIGURATION (R3) ---
OPENSTACK_CONFIG = {
    "auth_url": os.getenv("OPENSTACK_AUTH_URL", "http://192.168.201.11:5000/v3"),
    "project_name": os.getenv("OPENSTACK_PROJECT_NAME", "admin"),
    "username": os.getenv("OPENSTACK_USERNAME", "admin"),
    "password": os.getenv("OPENSTACK_PASSWORD", "secret"),
    "user_domain_name": os.getenv("OPENSTACK_USER_DOMAIN_NAME", "Default"),
    "project_domain_name": os.getenv("OPENSTACK_PROJECT_DOMAIN_NAME", "Default"),
    "external_network": os.getenv("OPENSTACK_EXTERNAL_NETWORK", "external"),
    # NUEVO: Nombre de la interfaz física para VLANs (R5)
    # Suele ser 'physnet1', 'default', 'public' o 'provider'. 
    # En entornos de lab con una sola interfaz, suele coincidir con la external.
    "physical_network": os.getenv("OPENSTACK_PROVIDER_PHYSICAL_NETWORK", "physnet1") 
}