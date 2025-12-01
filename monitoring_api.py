from fastapi import FastAPI, HTTPException, Depends
import httpx
from sshtunnel import SSHTunnelForwarder
import time
import asyncio
import logging
from typing import Union, Dict, Any, List
# Nota: Asumo que 'utils.database' y 'utils.logger' existen y son funcionales.
from sqlalchemy.orm import Session
from utils.database import get_db
from utils.logger import log_entry

# Configuración de logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Prometheus Metrics Service (Async)", version="2.1")

# --- CONFIGURACIÓN DE TÚNELES MÚLTIPLES ---
SSH_USER = "ubuntu"
SSH_PASSWORD = "RedesCloud2025"
REMOTE_PROM_PORT = 9090

# Configuración OVS1 (10.20.12.154:5815)
OVS1_CONFIG = {
    "host": "10.20.12.154",
    "port": 5815,
    "local_port": 9090,
    "job_name": "nodes"
}

# Configuración OVS2 (10.20.12.154:5825)
OVS2_CONFIG = {
    "host": "10.20.12.154",
    "port": 5825,
    "local_port": 9091,
    "job_name": "node_exporter"  # Asegúrese que este job name sea correcto en su prometheus.yml de OVS2
}

# Inicialización global de recursos
RESOURCES: Dict[str, Any] = {
    "OVS1": {"server": None, "http_client": None, "url": "", "config": OVS1_CONFIG},
    "OVS2": {"server": None, "http_client": None, "url": "", "config": OVS2_CONFIG},
}


def setup_tunnel(name: str, config: Dict[str, Any]):
    """Configura e inicia un túnel SSH y un cliente HTTP para un recurso específico."""
    try:
        logger.info(f"[{name}] Starting SSH Tunnel...")
        server = SSHTunnelForwarder(
            (config['host'], config['port']),
            ssh_username=SSH_USER,
            ssh_password=SSH_PASSWORD,
            remote_bind_address=('127.0.0.1', REMOTE_PROM_PORT),
            local_bind_address=('127.0.0.1', config['local_port'])
        )
        server.start()
        time.sleep(1)

        prom_url = f"http://{server.local_bind_host}:{server.local_bind_port}/api/v1"
        http_client = httpx.AsyncClient(base_url=prom_url, timeout=10.0)

        RESOURCES[name]["server"] = server
        RESOURCES[name]["http_client"] = http_client
        RESOURCES[name]["url"] = prom_url

        logger.info(f"[{name}] SSH Tunnel started successfully. Prometheus accessible at {prom_url}")
        return True
    except Exception as e:
        logger.error(f"FATAL: [{name}] Could not start SSH Tunnel or Async Client: {e}")
        RESOURCES[name]["server"] = None
        RESOURCES[name]["http_client"] = None
        return False


@app.on_event("startup")
def startup_event():
    """Inicia los túneles SSH y los clientes HTTP al arrancar la aplicación."""
    setup_tunnel("OVS1", OVS1_CONFIG)
    setup_tunnel("OVS2", OVS2_CONFIG)


@app.on_event("shutdown")
def shutdown_event():
    """Cierra los clientes HTTP y los túneles SSH al apagar la aplicación."""
    logger.info("Shutting down resources...")
    for name, res in RESOURCES.items():
        if res["http_client"]:
            asyncio.run(res["http_client"].aclose())
        if res["server"] and res["server"].is_active:
            res["server"].stop()
        logger.info(f"[{name}] Resources closed.")


async def get_metric(http_client: httpx.AsyncClient, query: str, inst: str, db: Session = None) -> Union[float, None]:
    """Ejecuta una consulta PromQL usando el cliente HTTP provisto."""
    if not http_client:
        logger.error(f"[{inst}] HTTP client not initialized for query.")
        return None

    try:
        resp = await http_client.get("/query", params={'query': query})
        resp.raise_for_status()
        data = resp.json()

        if data['status'] == 'success' and data['data']['result']:
            value = float(data['data']['result'][0]['value'][1])
            logger.debug(f"[{inst}] Query OK. Value: {value}")
            if db: log_entry(db, "MonitoringAPI", "DEBUG", f"Metric value: {value}", None)
            return value
    except httpx.HTTPStatusError as e:
        logger.error(f"[{inst}] Prometheus API returned error: {e}")
        if db: log_entry(db, "MonitoringAPI", "ERROR", f"Prometheus API error: {e}", None)
    except httpx.ConnectTimeout as e:
        logger.error(f"[{inst}] Connection to Prometheus timed out: {e}")
        if db: log_entry(db, "MonitoringAPI", "ERROR", f"Prometheus timeout: {e}", None)
    except Exception as e:
        logger.error(f"[{inst}] General error getting metric: {e}")
        if db: log_entry(db, "MonitoringAPI", "ERROR", f"Error getting metric: {e}", None)
    return None


async def get_active_instances(http_client: httpx.AsyncClient, job_name: str, db: Session = None) -> List[str]:
    """Obtiene las instancias activas para un job específico."""
    if not http_client:
        return []

    try:
        resp = await http_client.get("/targets")
        resp.raise_for_status()
        data = resp.json()

        instances = []
        for target in data['data']['activeTargets']:
            if target['labels'].get('job') == job_name and target['health'] == 'up':
                instances.append(target['labels']['instance'])

        logger.info(f"[{job_name}] Active instances found: {instances}")
        if db: log_entry(db, "MonitoringAPI", "INFO", f"Active instances: {instances}", None)
        return instances
    except Exception as e:
        logger.error(f"[{job_name}] Error obteniendo instancias activas: {e}")
        if db: log_entry(db, "MonitoringAPI", "ERROR", f"Error getting active instances: {e}", None)
        return []


# -----------------------------
# OVS1 (Hosts Normales) - INCLUYE USO ACTUAL
# -----------------------------
async def get_host_metrics(http_client: httpx.AsyncClient, inst: str, db: Session = None) -> Dict[str, Any]:
    """
    Obtiene las métricas de capacidad total y **uso actual** de un host (OVS1).
    """
    ip = inst.split(":")[0]
    last_octet = ip.split(".")[-1]
    host_name = f"host{last_octet}"

    # Definimos todas las consultas a ejecutar (CAPACIDAD TOTAL + USO ACTUAL)
    queries = {
        "cpu_cores": f'count(node_cpu_seconds_total{{instance="{inst}"}})',
        "mem_total": f'node_memory_MemTotal_bytes{{instance="{inst}"}}',
        "disk_total": f'node_filesystem_size_bytes{{instance="{inst}",mountpoint="/",fstype!="tmpfs",fstype!="overlay"}}',
        "availability": f'avg_over_time(up{{instance="{inst}"}}[1h])',

        # --- USO ACTUAL (para el cálculo de fitness) ---
        "cpu_used_ratio": f'1 - avg by (instance) (rate(node_cpu_seconds_total{{mode="idle",instance="{inst}"}}[5m]))',
        "mem_available": f'node_memory_MemAvailable_bytes{{instance="{inst}"}}',
        "disk_available": f'node_filesystem_avail_bytes{{instance="{inst}",mountpoint="/",fstype!="tmpfs",fstype!="overlay"}}',
    }

    # Ejecutamos todas las promesas de métricas de forma concurrente
    tasks = [get_metric(http_client, q, inst, db) for q in queries.values()]
    results = await asyncio.gather(*tasks)
    metrics = dict(zip(queries.keys(), results))

    # Totales en Bytes
    cpu_cores = metrics.get("cpu_cores", 1)
    mem_total = metrics.get("mem_total", 0)
    disk_total = metrics.get("disk_total", 0)

    # Valores de Disponibilidad/Uso
    availability = metrics.get("availability", 1.0)
    cpu_used_ratio = metrics.get("cpu_used_ratio", 0.0)
    mem_available = metrics.get("mem_available", 0)
    disk_available = metrics.get("disk_available", 0)

    # Conversion (Totales a MB y GB)
    ram_total_mb = round(mem_total / (1024 * 1024), 0) if mem_total else 0
    storage_total_gb = round(disk_total / (1024 * 1024 * 1024), 0) if disk_total else 0

    # Conversion (Usados a Cores, MB y GB)
    cpu_used_cores = round(cpu_cores * cpu_used_ratio, 2)
    ram_used_mb = round((mem_total - mem_available) / (1024 * 1024),
                        0) if mem_total > 0 and mem_available is not None else 0
    storage_used_gb = round((disk_total - disk_available) / (1024 * 1024 * 1024),
                            0) if disk_total > 0 and disk_available is not None else 0

    return {
        "id": host_name,
        "ip": ip,
        "cpu_total": cpu_cores,
        "ram_total_mb": ram_total_mb,
        "storage_total_gb": storage_total_gb,
        "availability": round(availability, 3),
        "power_idle": 100.0,
        "power_max": 250.0,
        # --- CAMPOS DE USO ACTUAL ---
        "cpu_used_cores": cpu_used_cores,
        "ram_used_mb": ram_used_mb,
        "storage_used_gb": storage_used_gb,
    }


# -----------------------------
# OVS2 (Nodos OpenStack) - INCLUYE USO ACTUAL
# -----------------------------
async def get_openstack_node_metrics(http_client: httpx.AsyncClient, inst: str, db: Session = None) -> Dict[str, Any]:
    """
    Obtiene las métricas de capacidad total y **uso actual** de un host de OpenStack (OVS2).
    """
    ip = inst.split(":")[0]
    host_name = f"openstack_node_{ip.split('.')[-1]}"

    # Definimos las consultas a ejecutar (CAPACIDAD TOTAL + USO ACTUAL)
    queries = {
        "cpu_cores": f'count(node_cpu_seconds_total{{instance="{inst}"}})',
        "mem_total": f'node_memory_MemTotal_bytes{{instance="{inst}"}}',
        "disk_total": f'node_filesystem_size_bytes{{instance="{inst}",mountpoint="/",fstype!="tmpfs",fstype!="overlay"}}',
        "availability": f'avg_over_time(up{{instance="{inst}"}}[1h])',

        # --- USO ACTUAL (para el cálculo de fitness) ---
        "cpu_used_ratio": f'1 - avg by (instance) (rate(node_cpu_seconds_total{{mode="idle",instance="{inst}"}}[5m]))',
        "mem_available": f'node_memory_MemAvailable_bytes{{instance="{inst}"}}',
        "disk_available": f'node_filesystem_avail_bytes{{instance="{inst}",mountpoint="/",fstype!="tmpfs",fstype!="overlay"}}',
    }

    # Ejecutamos las promesas de métricas de forma concurrente
    tasks = [get_metric(http_client, q, inst, db) for q in queries.values()]
    results = await asyncio.gather(*tasks)
    metrics = dict(zip(queries.keys(), results))

    # Totales en Bytes
    cpu_cores = metrics.get("cpu_cores", 1)
    mem_total = metrics.get("mem_total", 0)
    disk_total = metrics.get("disk_total", 0)

    # Valores de Disponibilidad/Uso
    availability = metrics.get("availability", 1.0)
    cpu_used_ratio = metrics.get("cpu_used_ratio", 0.0)
    mem_available = metrics.get("mem_available", 0)
    disk_available = metrics.get("disk_available", 0)

    # Conversion (Totales a MB y GB)
    ram_total_mb = round(mem_total / (1024 * 1024), 0) if mem_total else 0
    storage_total_gb = round(disk_total / (1024 * 1024 * 1024), 0) if disk_total else 0

    # Conversion (Usados a Cores, MB y GB)
    cpu_used_cores = round(cpu_cores * cpu_used_ratio, 2)
    ram_used_mb = round((mem_total - mem_available) / (1024 * 1024),
                        0) if mem_total > 0 and mem_available is not None else 0
    storage_used_gb = round((disk_total - disk_available) / (1024 * 1024 * 1024),
                            0) if disk_total > 0 and disk_available is not None else 0

    return {
        "id": host_name,
        "ip": ip,
        "cpu_total": cpu_cores,  # Total cores (vCPUs)
        "ram_total_mb": ram_total_mb,  # Total RAM en MB
        "storage_total_gb": storage_total_gb,  # Total Storage en GB
        "availability": round(availability, 3),
        "power_idle": 150.0,  # Valores diferentes por ser nodos de OpenStack
        "power_max": 300.0,
        # --- CAMPOS DE USO ACTUAL ---
        "cpu_used_cores": cpu_used_cores,
        "ram_used_mb": ram_used_mb,
        "storage_used_gb": storage_used_gb,
    }


async def get_hosts_from_prometheus(resource_name: str, db: Session = None) -> List[Dict[str, Any]]:
    """Función unificada para obtener hosts de forma concurrente de un recurso."""
    res = RESOURCES[resource_name]
    http_client = res["http_client"]
    job_name = res["config"]["job_name"]

    if not http_client:
        logger.error(f"[{resource_name}] HTTP client is not active.")
        return []

    # 1. Obtener instancias activas (asíncrono)
    instances = await get_active_instances(http_client, job_name, db)

    if not instances:
        logger.warning(f"[{resource_name}] No active instances found in Prometheus.")
        return []

    # 2. Crear una tarea de obtención de métricas por cada instancia
    if resource_name == "OVS1":
        tasks = [get_host_metrics(http_client, inst, db) for inst in instances]
    else:  # OVS2 (OpenStack)
        tasks = [get_openstack_node_metrics(http_client, inst, db) for inst in instances]

    # 3. Ejecutar todas las tareas de forma concurrente
    hosts = await asyncio.gather(*tasks)

    return hosts


# --- ENDPOINTS DE FASTAPI ---

@app.get("/hosts")
async def get_ovs1_hosts(db: Session = Depends(get_db)):
    """Endpoint que devuelve el estado actual y uso de los hosts detectados en OVS1."""
    try:
        hosts_data = await get_hosts_from_prometheus("OVS1", db)
        return {"hosts": hosts_data}
    except Exception as e:
        logger.error(f"Unhandled error in /hosts (OVS1) endpoint: {e}")
        raise HTTPException(status_code=503, detail="Service Unavailable: Could not fetch OVS1 host metrics.")


@app.get("/hosts_openstack")
async def get_ovs2_hosts(db: Session = Depends(get_db)):
    """Endpoint asíncrono que devuelve el estado actual y uso de los nodos de OpenStack detectados en OVS2."""
    try:
        hosts_data = await get_hosts_from_prometheus("OVS2", db)
        return {"hosts": hosts_data}
    except Exception as e:
        logger.error(f"Unhandled error in /hosts_openstacks (OVS2) endpoint: {e}")
        raise HTTPException(status_code=503, detail="Service Unavailable: Could not fetch OVS2 OpenStack host metrics.")


@app.get("/health")
async def health_check():
    """Verifica el estado de ambos túneles SSH y la conexión a Prometheus."""
    health_status = {}
    service_available = True

    for name, res in RESOURCES.items():
        is_tunnel_up = res["server"] is not None and res["server"].is_active
        health_status[name] = {"tunnel_status": "up" if is_tunnel_up else "down"}

        if is_tunnel_up and res["http_client"]:
            try:
                # Intentar una consulta muy simple y rápida a la API de Prometheus
                response = await res["http_client"].get("/status/runtimeinfo")
                response.raise_for_status()
                health_status[name]["prometheus_access"] = "success"
            except Exception:
                health_status[name]["prometheus_access"] = "unreachable or slow"
                service_available = False
        else:
            health_status[name]["prometheus_access"] = "N/A (Tunnel Down)"
            service_available = False

    if not service_available:
        raise HTTPException(status_code=503, detail={"status": "Service Unavailable", "details": health_status})

    return {"status": "ok", "details": health_status}