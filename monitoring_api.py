from fastapi import FastAPI, HTTPException, Depends
import httpx  # Necesitas instalarlo: pip install httpx
from sshtunnel import SSHTunnelForwarder
import time
import asyncio
import logging
from typing import Union  # Importación necesaria para Python < 3.10
from sqlalchemy.orm import Session
from utils.database import get_db
from utils.logger import log_entry

# Configuración de logs para un mejor debug
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Prometheus Metrics Service (Async)", version="2.0")

# Configuración SSH al servidor OVS
SSH_HOST = "10.20.12.154"
SSH_PORT = 5815
SSH_USER = "ubuntu"
SSH_PASSWORD = "RedesCloud2025"

# Puerto remoto donde Prometheus está corriendo en el servidor remoto
REMOTE_PROM_PORT = 9090

# Inicialización global del cliente HTTP asíncrono y el túnel SSH
# Uso de Union[...] para compatibilidad con Python 3.8
server: Union[SSHTunnelForwarder, None] = None
http_client: Union[httpx.AsyncClient, None] = None
PROM_URL: str = ""


@app.on_event("startup")
def startup_event():
    """Inicia el túnel SSH y el cliente HTTP al arrancar la aplicación."""
    global server, http_client, PROM_URL
    try:
        logger.info("Starting SSH Tunnel...")
        server = SSHTunnelForwarder(
            (SSH_HOST, SSH_PORT),
            ssh_username=SSH_USER,
            ssh_password=SSH_PASSWORD,
            remote_bind_address=('127.0.0.1', REMOTE_PROM_PORT),
            local_bind_address=('127.0.0.1', 9090)
        )
        server.start()
        # Espera para asegurar que el túnel esté levantado
        time.sleep(1)

        PROM_URL = f"http://{server.local_bind_host}:{server.local_bind_port}/api/v1"
        # Inicializa httpx para peticiones asíncronas
        http_client = httpx.AsyncClient(base_url=PROM_URL, timeout=10.0)
        logger.info(f"SSH Tunnel started successfully. Prometheus accessible at {PROM_URL}")

    except Exception as e:
        logger.error(f"FATAL: Could not start SSH Tunnel or Async Client: {e}")
        # En un entorno de producción, esto debería detener el servicio.


@app.on_event("shutdown")
def shutdown_event():
    """Cierra el cliente HTTP y el túnel SSH al apagar la aplicación."""
    global server, http_client
    logger.info("Shutting down resources...")
    if http_client:
        asyncio.run(http_client.aclose())  # Cierra el cliente httpx
    if server and server.is_active:
        server.stop()
    logger.info("Resources closed.")


async def get_metric(query: str, inst: str, db: Session = None):
    """Ejecuta una consulta PromQL y retorna el valor numérico. Usa httpx."""
    if not http_client:
        logger.error("HTTP client not initialized.")
        return None

    try:
        # Usamos el cliente asíncrono para la petición
        resp = await http_client.get("/query", params={'query': query})
        resp.raise_for_status()  # Lanza excepción si el status no es 2xx
        data = resp.json()

        if data['status'] == 'success' and data['data']['result']:
            # El resultado es un vector de tupla [timestamp, value]
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


async def get_active_instances(db: Session = None):
    """Obtiene las instancias activas de node_exporter registradas en Prometheus."""
    if not http_client:
        return []

    try:
        resp = await http_client.get("/targets")
        resp.raise_for_status()
        data = resp.json()

        instances = []
        # Filtramos solo por targets activos (up) y del job 'nodes'
        for target in data['data']['activeTargets']:
            if target['labels'].get('job') == 'nodes' and target['health'] == 'up':
                instances.append(target['labels']['instance'])

        logger.info(f"Active instances found: {instances}")
        if db: log_entry(db, "MonitoringAPI", "INFO", f"Active instances: {instances}", None)
        return instances
    except Exception as e:
        logger.error(f"Error obteniendo instancias activas: {e}")
        if db: log_entry(db, "MonitoringAPI", "ERROR", f"Error getting active instances: {e}", None)
        return []


async def get_host_metrics(inst: str, db: Session = None):
    """
    Obtiene las métricas de capacidad total de un host de forma concurrente.
    Retorna RAM en MB y Storage en GB.
    """
    ip = inst.split(":")[0]
    last_octet = ip.split(".")[-1]
    host_name = f"host{last_octet}"

    # Definimos todas las consultas a ejecutar (CAPACIDAD TOTAL)
    queries = {
        # Contar el número de núcleos (vCPUs)
        "cpu_cores": f'count(node_cpu_seconds_total{{instance="{inst}"}})',
        # RAM Total en bytes
        "mem_total": f'node_memory_MemTotal_bytes{{instance="{inst}"}}',
        # Disco Total en bytes (usamos el punto de montaje principal '/')
        "disk_total": f'node_filesystem_size_bytes{{instance="{inst}",mountpoint="/",fstype!="tmpfs",fstype!="overlay"}}',
        # Disponibilidad
        "availability": f'avg_over_time(up{{instance="{inst}"}}[1h])',
    }

    # Ejecutamos todas las promesas de métricas de forma concurrente
    tasks = [get_metric(q, inst, db) for q in queries.values()]

    # results contendrá los valores de las métricas en el mismo orden que las queries
    results = await asyncio.gather(*tasks)

    # Mapeamos los resultados a un diccionario
    metrics = dict(zip(queries.keys(), results))

    # Totales en Bytes
    cpu_cores = metrics["cpu_cores"] if metrics["cpu_cores"] is not None else 1
    mem_total = metrics["mem_total"]
    disk_total = metrics["disk_total"]

    # Conversion (Bytes a MB y GB)
    # RAM en MB
    ram_total_mb = round(mem_total / (1024 * 1024), 0) if mem_total else 0
    # Storage en GB
    storage_total_gb = round(disk_total / (1024 * 1024 * 1024), 0) if disk_total else 0

    # Availability
    availability = metrics["availability"] if metrics["availability"] is not None else 1.0

    return {
        "id": host_name,
        "ip": ip,
        # CAPACIDAD TOTAL
        "cpu_total": cpu_cores,  # Total cores (vCPUs)
        "ram_total_mb": ram_total_mb,  # Total RAM en MB
        "storage_total_gb": storage_total_gb,  # Total Storage en GB

        # OTROS METRICS
        "availability": round(availability, 3),
        # Valores constantes para compatibilidad con el API de Placement
        "power_idle": 100.0,
        "power_max": 250.0,
    }


async def get_hosts_from_prometheus(db: Session = None):
    """Función principal para obtener hosts de forma concurrente."""

    # 1. Obtener instancias activas (asíncrono)
    instances = await get_active_instances(db)

    if not instances:
        logger.warning("No active instances found in Prometheus.")
        return []

    # 2. Crear una tarea de obtención de métricas por cada instancia
    tasks = [get_host_metrics(inst, db) for inst in instances]

    # 3. Ejecutar todas las tareas de forma concurrente
    hosts = await asyncio.gather(*tasks)

    return hosts


@app.get("/hosts")
async def get_hosts(db: Session = Depends(get_db)):
    """Endpoint asíncrono que devuelve el estado actual de los hosts detectados."""
    try:
        hosts_data = await get_hosts_from_prometheus(db)
        return {"hosts": hosts_data}
    except Exception as e:
        logger.error(f"Unhandled error in /hosts endpoint: {e}")
        # Levantamos una excepción 503 si el túnel falló o hay un problema grave
        raise HTTPException(status_code=503, detail="Service Unavailable: Could not fetch host metrics.")


@app.get("/health")
async def health_check():
    """Verifica el estado del túnel SSH y la conexión a Prometheus."""
    # Uso de Union[...] para compatibilidad con Python 3.8
    if server is None or not server.is_active:
        raise HTTPException(status_code=503, detail="SSH Tunnel is down.")

    try:
        # Intentar una consulta muy simple y rápida a la API de Prometheus
        response = await http_client.get("/status/runtimeinfo")
        response.raise_for_status()
        return {"status": "ok", "prometheus_access": "success"}
    except Exception:
        raise HTTPException(status_code=503, detail="SSH Tunnel is up, but Prometheus is unreachable or slow.")
