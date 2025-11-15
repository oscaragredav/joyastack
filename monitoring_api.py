from fastapi import FastAPI
import requests
from sshtunnel import SSHTunnelForwarder
import time

app = FastAPI(title="Prometheus Metrics Service", version="1.0")

# Configuración SSH al servidor OVS
SSH_HOST = "10.20.12.154"
SSH_PORT = 5815
SSH_USER = "ubuntu"
SSH_PASSWORD = "RedesCloud2025"

# Puerto remoto donde Prometheus está corriendo en el servidor remoto
REMOTE_PROM_PORT = 9090

# Abrir túnel SSH al iniciar la app
server = SSHTunnelForwarder(
    (SSH_HOST, SSH_PORT),
    ssh_username=SSH_USER,
    ssh_password=SSH_PASSWORD,
    remote_bind_address=('127.0.0.1', REMOTE_PROM_PORT),
    local_bind_address=('127.0.0.1', 9090)
)

server.start()
time.sleep(1)

PROM_URL = f"http://{server.local_bind_host}:{server.local_bind_port}/api/v1"


def get_metric(query):
    """Ejecuta una consulta PromQL y retorna el valor numérico."""
    try:
        resp = requests.get(f"{PROM_URL}/query", params={'query': query})
        data = resp.json()
        print(f"Query: {query[:80]}...")  # Debug: ver query
        print(f"Response status: {data.get('status')}")  # Debug: ver respuesta
        print(f"Results: {len(data.get('data', {}).get('result', []))} results")  # Debug

        if data['status'] == 'success' and data['data']['result']:
            value = float(data['data']['result'][0]['value'][1])
            print(f"Value returned: {value}")  # Debug: ver valor
            return value
    except Exception as e:
        print(f"Error obteniendo métrica: {e}")
        print(f"Query was: {query}")
    return None


def get_active_instances():
    """Obtiene las instancias activas de node_exporter registradas en Prometheus."""
    try:
        resp = requests.get(f"{PROM_URL}/targets")
        data = resp.json()

        instances = []
        for target in data['data']['activeTargets']:
            if target['labels'].get('job') == 'nodes' and target['health'] == 'up':
                instances.append(target['labels']['instance'])

        print(f"Active instances found: {instances}")  # Debug
        return instances
    except Exception as e:
        print(f"Error obteniendo instancias activas: {e}")
        return []


def get_hosts_from_prometheus():
    hosts = []
    instances = get_active_instances()

    for inst in instances:
        print(f"\n=== Processing instance: {inst} ===")  # Debug
        ip = inst.split(":")[0]
        last_octet = ip.split(".")[-1]
        host_name = f"host{last_octet}"

        # CPU
        cpu_idle = get_metric(
            f'avg by (instance) (rate(node_cpu_seconds_total{{mode="idle",instance="{inst}"}}[2m])) * 100')
        cpu_usage = 100 - cpu_idle if cpu_idle is not None else None

        # RAM
        mem_total = get_metric(f'node_memory_MemTotal_bytes{{instance="{inst}"}}')
        mem_avail = get_metric(f'node_memory_MemAvailable_bytes{{instance="{inst}"}}')
        ram_usage = ((mem_total - mem_avail) / mem_total) * 100 if mem_total and mem_avail else None

        # Storage
        disk_avail = get_metric(f'node_filesystem_avail_bytes{{instance="{inst}",fstype!="tmpfs",fstype!="overlay"}}')
        disk_total = get_metric(f'node_filesystem_size_bytes{{instance="{inst}",fstype!="tmpfs",fstype!="overlay"}}')
        storage_usage = ((disk_total - disk_avail) / disk_total) * 100 if disk_total and disk_avail else None

        # Availability
        availability = get_metric(f'avg_over_time(up{{instance="{inst}"}}[1h])') or 1.0

        hosts.append({
            "id": host_name,
            "ip": ip,
            "cpu": round(cpu_usage, 2) if cpu_usage is not None else 0,
            "ram": round(ram_usage, 2) if ram_usage is not None else 0,
            "storage": round(storage_usage, 2) if storage_usage is not None else 0,
            "availability": round(availability, 3),
            "power_idle": 100,
            "power_max": 250
        })
    return hosts


@app.get("/hosts")
def get_hosts():
    """Endpoint que devuelve el estado actual de los hosts detectados."""
    return {"hosts": get_hosts_from_prometheus()}