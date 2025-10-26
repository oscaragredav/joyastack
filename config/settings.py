import os

# --- Configuración de base de datos ---
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "root")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "joyastack")

DB_URL = f"mysql+mysqlconnector://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# --- Otros parámetros globales ---
WORKER_IPS = ["10.0.10.2", "10.0.10.3", "10.0.10.4"]
SSH_USER = "ubuntu"
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", "/home/ubuntu/.ssh/id_rsa")
