import json
import time

from fastapi import FastAPI, HTTPException, Header, Request
import random
import requests
from typing import List, Dict, Optional
import logging

from starlette.middleware.cors import CORSMiddleware

# Configurar logging detallado
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Improved Genetic Algorithm for VM Placement", version="I-GA 1.0")

# ============================================
# CONFIGURAR CORS (UNA SOLA VEZ)
# ============================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# MIDDLEWARE PARA DEBUG (UNA SOLA VEZ)
# ============================================
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    logger.info(f"🔵 INCOMING: {request.method} {request.url.path}")

    response = await call_next(request)

    duration = time.time() - start_time
    logger.info(f"🟢 RESPONSE: Status {response.status_code} | {duration:.2f}s")

    return response


# -----------------------------
# Función auxiliar para obtener hosts
# -----------------------------
def get_hosts():
    """
    Obtiene la lista de hosts disponibles desde la API de recursos (8003)
    y normaliza las unidades, incluyendo campos de carga base (Prometheus).
    """
    try:
        resp = requests.get("http://10.20.12.32:8003/hosts", timeout=30)
        resp.raise_for_status()
        data = resp.json()

        hosts_data = data["hosts"] if isinstance(data, dict) and "hosts" in data else data

        normalized_hosts = []
        for h in hosts_data:
            normalized_hosts.append({
                "id": h["id"],
                "ip": h["ip"],
                # Capacidad total:
                "cpu": h.get("cpu_total", 0),
                "ram": h.get("ram_total_mb", 0),
                "storage": h.get("storage_total_gb", 0),
                # Metadata:
                "availability": h.get("availability", 1.0),
                "power_idle": h.get("power_idle", 100),
                "power_max": h.get("power_max", 250),
                # 🆕 Carga Base Actual (Asumida del Monitoring/Prometheus):
                "cpu_used_cores": h.get("cpu_used_cores", 0),
                "ram_used_mb": h.get("ram_used_mb", 0),
                "storage_used_gb": h.get("storage_used_gb", 0)
            })

        logger.info(f"✓ Obtenidos {len(normalized_hosts)} hosts desde API de recursos (con carga base)")
        return normalized_hosts
    except Exception as e:
        logger.error(f"✗ Error obteniendo hosts: {e}")
        return []
# -----------------------------
# Parámetros del sistema
# -----------------------------
CPU_OVER = 1.2
RAM_OVER = 1.5
STORAGE_OVER = 1.0

# Parámetros del algoritmo I-GA
POP_SIZE = 50
GENERATIONS = 100
ELITE_SIZE = 5
MUTATION_RATE = 0.2


# -----------------------------
# VALIDACIÓN DE RECURSOS
# -----------------------------
class ResourceValidationError(Exception):
    """Excepción personalizada para errores de validación de recursos"""
    pass


def validate_resources(vms, hosts):
    """
    Valida que las VMs puedan ser asignadas a los hosts disponibles.
    (Unidades RAM: MB, Storage: GB)
    """
    # Calcular recursos totales requeridos
    total_cpu_required = sum(vm["cpu"] for vm in vms)
    total_ram_required = sum(vm["ram"] for vm in vms)
    total_storage_required = sum(vm["storage"] for vm in vms)

    # Calcular recursos totales disponibles (con overcommit)
    total_cpu_available = sum(h["cpu"] * CPU_OVER for h in hosts)
    total_ram_available = sum(h["ram"] * RAM_OVER for h in hosts)
    total_storage_available = sum(h["storage"] * STORAGE_OVER for h in hosts)

    # Validar CPU
    if total_cpu_required > total_cpu_available:
        cpu_usage = (total_cpu_required / total_cpu_available) * 100
        raise ResourceValidationError(
            f"CPU insuficiente: Se requiere {total_cpu_required} vCPUs pero solo hay "
            f"{total_cpu_available:.2f} disponibles (uso: {cpu_usage:.1f}%)"
        )

    # Validar RAM (Unidad: MB)
    if total_ram_required > total_ram_available:
        ram_usage = (total_ram_required / total_ram_available) * 100
        raise ResourceValidationError(
            f"RAM insuficiente: Se requiere {total_ram_required} MB pero solo hay "
            f"{total_ram_available:.2f} MB disponibles (uso: {ram_usage:.1f}%)"
        )

    # Validar Storage (Unidad: GB)
    if total_storage_required > total_storage_available:
        storage_usage = (total_storage_required / total_storage_available) * 100
        raise ResourceValidationError(
            f"Almacenamiento insuficiente: Se requiere {total_storage_required} GB pero solo hay "
            f"{total_storage_available:.2f} GB disponibles (uso: {storage_usage:.1f}%)"
        )

    # Calcular porcentajes de uso
    cpu_percent = (total_cpu_required / total_cpu_available) * 100
    ram_percent = (total_ram_required / total_ram_available) * 100
    storage_percent = (total_storage_required / total_storage_available) * 100

    logger.info(f"✓ Validación de recursos aprobada:")
    logger.info(f"  - CPU: {cpu_percent:.1f}% ({total_cpu_required}/{total_cpu_available:.2f} vCPUs)")
    logger.info(f"  - RAM: {ram_percent:.1f}% ({total_ram_required}/{total_ram_available:.2f} MB)")
    logger.info(f"  - Storage: {storage_percent:.1f}% ({total_storage_required}/{total_storage_available:.2f} GB)")

    return True


def validate_single_vm_fits(vm, hosts):
    """
    Valida que al menos exista un host que pueda alojar la VM individualmente.
    (Unidades RAM: MB, Storage: GB)
    """
    for host in hosts:
        if (vm["cpu"] <= host["cpu"] * CPU_OVER and
                vm["ram"] <= host["ram"] * RAM_OVER and
                vm["storage"] <= host["storage"] * STORAGE_OVER):
            return True

    # Mensaje de error corregido para usar MB y GB
    raise ResourceValidationError(
        f"La VM '{vm['id']}' es demasiado grande: requiere {vm['cpu']} vCPUs, "
        f"{vm['ram']} MB RAM, {vm['storage']} GB storage. Ningún host individual puede alojarla."
    )


# -----------------------------
# Funciones del algoritmo
# -----------------------------
def energy_consumption(usage_ratio, host):
    """Modelo simple de energía basado en el paper (ecuación 8)."""
    # Asegura la saturación a P_max
    effective_ratio = min(1.0, usage_ratio)
    return host["power_idle"] + (host["power_max"] - host["power_idle"]) * (effective_ratio ** 3)

def availability_product(used_hosts):
    """Disponibilidad total multiplicada (ecuación 13 del paper)."""
    prod = 1.0
    for h in used_hosts:
        prod *= h.get("availability", 1.0) # Uso .get() para seguridad
    return prod


def preprocess_hosts(hosts):
    """Aplica VHAM (Virtual Host Availability Model) con overcommit y clustering virtual"""
    for h in hosts:
        h["cpu_virtual"] = h["cpu"] * CPU_OVER
        h["ram_virtual"] = h["ram"] * RAM_OVER
        h["storage_virtual"] = h["storage"] * STORAGE_OVER

    max_cpu_v = max(h["cpu_virtual"] for h in hosts)
    max_power = max(h["power_max"] for h in hosts)

    for h in hosts:
        h["vham_score"] = (
                0.6 * (h["cpu_virtual"] / max_cpu_v) +
                0.3 * h["availability"] -
                0.1 * (h["power_max"] / max_power)
        )

    hosts.sort(key=lambda x: x["vham_score"], reverse=True)
    return hosts


# -----------------------------
# NUEVA LÓGICA DE FITNESS (CRUCIALMENTE MODIFICADA)
# -----------------------------
def fitness(chromosome, vms, hosts):
    """
    Calcula el fitness F = 1 / G_T (ecuación 16),
    incluyendo la carga base actual y aplicando **restricciones de RAM/Storage**. (MODIFICADA)
    """
    # 1. Inicializar el uso con la carga base actual de Prometheus
    usage = {}
    for h in hosts:
        host_id = h["id"]
        # Usamos los campos de carga base (Prometheus)
        usage[host_id] = {
            "cpu": h.get("cpu_used_cores", 0),
            "ram": h.get("ram_used_mb", 0),
            "storage": h.get("storage_used_gb", 0)
        }

    # 2. Sumar la carga de las nuevas VMs y aplicar HARD CONSTRAINTS
    for i, vm in enumerate(vms):
        # El cromosoma[i] es el índice del host en la lista 'hosts'
        h = hosts[chromosome[i]]
        host_id = h["id"]

        usage[host_id]["cpu"] += vm["cpu"]
        usage[host_id]["ram"] += vm["ram"]
        usage[host_id]["storage"] += vm["storage"]

        # 🚨 FILTRADO CRUCIAL: RESTRICCIONES DURAS (RAM y Storage) 🚨
        # Si la carga TOTAL (base + nueva) excede la capacidad virtual (con overcommit).
        if (usage[host_id]["ram"] > h.get("ram_virtual", float('inf')) or
                usage[host_id]["storage"] > h.get("storage_virtual", float('inf'))):
            # Retorna infinito: el algoritmo genético lo descarta.
            return float("inf")

            # 3. Calcular la Energía Total (E^T) y G_T solo para cromosomas VIABLES
    active_hosts = []
    total_energy = 0.0

    # Asumimos que E_min ya está definido y disponible
    E_min = min(h.get("power_idle", 0.0) for h in hosts) if hosts else 0.0

    for h in hosts:
        host_id = h["id"]
        total_cpu_used = usage[host_id]["cpu"]

        # El host está activo si tiene carga (base o nueva)
        if total_cpu_used > 0:
            active_hosts.append(h)

            # Cálculo del Usage Ratio (Virtual)
            cpu_ratio = total_cpu_used / h["cpu_virtual"]

            total_energy += energy_consumption(cpu_ratio, h)

    if not active_hosts:
        return 1.0

        # 4. Calcular A_p y G_T
    availability = availability_product(active_hosts)

    if total_energy == 0.0:
        return 0.0

        # G_T = 0.5 * (Energy_Term + Availability_Term)
    G_T = 0.5 * ((E_min / total_energy) + availability)

    # F busca MINIMIZAR el resultado. Minimizar F -> Maximizar G_T.
    return 1 / G_T


def create_chromosome(vms, hosts):
    """Inicialización guiada por VHAM"""
    chrom = []
    for vm in vms:
        probs = [h["vham_score"] for h in hosts]
        s = sum(probs)
        probs = [p / s for p in probs]
        host_idx = random.choices(range(len(hosts)), weights=probs, k=1)[0]
        chrom.append(host_idx)
    return chrom


def crossover(p1, p2, n_vms):
    """Crossover jerárquico (por clusters)"""
    cluster_size = max(1, n_vms // 2)
    point = random.randint(0, cluster_size - 1)
    return p1[:point] + p2[point:]


def mutate(chrom, n_hosts):
    """Mutación adaptativa"""
    for i in range(len(chrom)):
        if random.random() < MUTATION_RATE:
            chrom[i] = random.randint(0, n_hosts - 1)
    return chrom


def run_genetic_algorithm(vms, hosts):
    """Ejecuta el algoritmo genético I-GA"""
    n_vms = len(vms)
    n_hosts = len(hosts)

    population = [create_chromosome(vms, hosts) for _ in range(POP_SIZE)]

    for gen in range(GENERATIONS):
        scored = [(chrom, fitness(chrom, vms, hosts)) for chrom in population]
        scored.sort(key=lambda x: x[1])
        elites = [x[0] for x in scored[:ELITE_SIZE]]

        new_population = elites.copy()
        while len(new_population) < POP_SIZE:
            p1, p2 = random.sample(elites, 2)
            child = crossover(p1, p2, n_vms)
            child = mutate(child, n_hosts)
            new_population.append(child)

        population = new_population

    best = min(population, key=lambda c: fitness(c, vms, hosts))
    return best


def build_placement_result(best_chromosome, vms, hosts):
    """Construye el resultado del placement"""
    placement = {h["id"]: [] for h in hosts}
    used = {h["id"]: {"cpu": 0, "ram": 0, "storage": 0} for h in hosts}

    for i, vm in enumerate(vms):
        h_id = hosts[best_chromosome[i]]["id"]
        placement[h_id].append(vm["id"])
        used[h_id]["cpu"] += vm["cpu"]
        used[h_id]["ram"] += vm["ram"]
        used[h_id]["storage"] += vm["storage"]

    usage_summary = []
    for h in hosts:
        # Nota: La ratio de uso aquí se calcula sobre la capacidad virtualizada.
        cpu_ratio = used[h["id"]]["cpu"] / h["cpu_virtual"]
        energy = energy_consumption(cpu_ratio, h) if cpu_ratio > 0 else 0
        usage_summary.append({
            "host_id": h["id"],
            "cpu_usage": round(cpu_ratio, 3),
            "energy": round(energy, 2),
            "availability": h["availability"],
            "assigned_vms": placement[h["id"]]
        })

    total_energy = sum(u["energy"] for u in usage_summary)
    active_hosts = [h for h in hosts if used[h["id"]]["cpu"] > 0]
    total_avail = availability_product(active_hosts) if active_hosts else 0

    return {
        "placements": usage_summary,
        "total_energy": round(total_energy, 2),
        "total_availability": round(total_avail, 4),
        "fitness_score": round(fitness(best_chromosome, vms, hosts), 4)
    }


# -----------------------------
# ENDPOINTS
# -----------------------------

@app.get("/placement")
def get_vm_placement():
    """Endpoint original con VMs hardcodeadas (para testing)"""
    # VMs ajustadas: RAM en MB (1GB = 1024MB), Storage en GB
    vms = [
        {"id": "vm1", "cpu": 4, "ram": 8192, "storage": 100},  # 8 GB RAM
        {"id": "vm2", "cpu": 6, "ram": 12288, "storage": 80},  # 12 GB RAM
        {"id": "vm3", "cpu": 8, "ram": 16384, "storage": 200},  # 16 GB RAM
        {"id": "vm4", "cpu": 3, "ram": 4096, "storage": 50}  # 4 GB RAM
    ]
    hosts = get_hosts()

    if not hosts:
        raise HTTPException(status_code=503, detail="No hay hosts disponibles")

    hosts = preprocess_hosts(hosts)

    try:
        # Validar que cada VM pueda caber en al menos un host
        for vm in vms:
            validate_single_vm_fits(vm, hosts)

        # Validar recursos totales
        validate_resources(vms, hosts)

    except ResourceValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    best = run_genetic_algorithm(vms, hosts)
    return build_placement_result(best, vms, hosts)


@app.post("/placement/slice/{slice_id}")
async def get_slice_placement(
        slice_id: int,
        request: Request,
        authorization: Optional[str] = Header(None)
):
    """
    Calcula el placement óptimo para las VMs de un slice específico.

    METODO PRINCIPAL: Recibe VMs en el body para evitar consulta circular al SliceManager
    """
    try:
        logger.info(f"🔍 [Slice {slice_id}] Procesando solicitud de placement")

        # ============================================
        # PASO 1: INTENTAR LEER VMS DEL BODY
        # ============================================
        vms_data = None
        try:
            body = await request.json()
            vms_data = body.get("vms")
            if vms_data:
                logger.info(f"✓ Recibidas {len(vms_data)} VMs en el body del request")
        except Exception as e:
            logger.warning(f"⚠️  No se pudo leer body: {e}")
            vms_data = None

        # ============================================
        # PASO 2: PROCESAR VMS
        # ============================================
        if vms_data and len(vms_data) > 0:
            # Usar VMs del body (evita consulta al SliceManager)
            vms = []
            for vm in vms_data:
                vms.append({
                    "id": vm.get("name") or f"vm_{vm['id']}",
                    "vm_id": vm["id"],
                    "cpu": vm.get("cpu", 1),
                    "ram": vm.get("ram", 1024),  # Default 1024 MB (1GB)
                    "storage": vm.get("disk", 3)  # Default 3 GB
                })

            logger.info(f"✓ VMs procesadas desde body: {[v['id'] for v in vms]}")

        else:
            # Fallback: consultar al SliceManager (solo si es necesario)
            logger.info(f"📡 Consultando SliceManager (fallback)...")

            token = authorization
            if not token:
                token = request.headers.get("Authorization") or request.headers.get("authorization")

            headers = {}
            if token:
                if not token.startswith("Bearer "):
                    headers["Authorization"] = f"Bearer {token}"
                else:
                    headers["Authorization"] = token

            try:
                resp = requests.get(
                    f"http://10.20.12.32:8001/slices/{slice_id}",
                    headers=headers,
                    timeout=10
                )

                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=resp.status_code,
                        detail=f"Error en SliceManager: {resp.text}"
                    )

                slice_data = resp.json()

                if "vms" not in slice_data or not slice_data["vms"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"El slice {slice_id} no tiene VMs definidas"
                    )

                vms = []
                for vm in slice_data["vms"]:
                    vms.append({
                        "id": vm.get("name") or f"vm_{vm['id']}",
                        "vm_id": vm["id"],
                        "cpu": vm.get("cpu", 1),
                        "ram": vm.get("ram", 1024),  # Default 1024 MB (1GB)
                        "storage": vm.get("disk", 3)  # Default 3 GB
                    })

            except requests.exceptions.Timeout:
                raise HTTPException(
                    status_code=504,
                    detail="Timeout conectando con SliceManager"
                )
            except requests.exceptions.ConnectionError:
                raise HTTPException(
                    status_code=503,
                    detail="No se pudo conectar con SliceManager"
                )

        # ============================================
        # PASO 3: OBTENER HOSTS Y VALIDAR RECURSOS
        # ============================================

        hosts = get_hosts()
        if not hosts:
            raise HTTPException(
                status_code=503,
                detail="No hay hosts disponibles en el Resource Manager"
            )

        hosts = preprocess_hosts(hosts)
        logger.info(f"✓ Hosts preprocesados: {len(hosts)}")

        # ============================================
        # VALIDACIÓN CRÍTICA DE RECURSOS
        # ============================================
        try:
            # Validar que cada VM pueda caber en al menos un host
            for vm in vms:
                validate_single_vm_fits(vm, hosts)

            # Validar recursos totales
            validate_resources(vms, hosts)

        except ResourceValidationError as e:
            logger.error(f"❌ Validación de recursos falló: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail=f"Recursos insuficientes para el slice {slice_id}: {str(e)}"
            )

        # ============================================
        # PASO 4: EJECUTAR I-GA
        # ============================================
        logger.info(f"🧬 Ejecutando algoritmo I-GA con {len(vms)} VMs...")
        best = run_genetic_algorithm(vms, hosts)

        result = build_placement_result(best, vms, hosts)

        # ============================================
        # PASO 5: ENRIQUECER RESULTADO
        # ============================================

        result["slice_id"] = slice_id
        result["total_vms"] = len(vms)

        logger.info(f"✅ Placement completado exitosamente")
        logger.info(f"   - Energía: {result['total_energy']} W")
        logger.info(f"   - Disponibilidad: {result['total_availability']}")
        logger.info(f"   - Fitness: {result['fitness_score']}")
        logger.info(f"📄 RESULTADO DE PLACEMENT JSON COMPLETO (OpenStack Slice {slice_id}):")
        logger.info(json.dumps(result, indent=4))
        return result

    except HTTPException:
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail=f"Error conectando con servicios: {str(e)}"
        )
    except Exception as e:
        logger.error(f"❌ Error inesperado: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error interno calculando placement: {str(e)}"
        )


@app.post("/placement/custom")
def get_custom_placement(request: Dict):
    """
    Calcula el placement óptimo para una lista personalizada de VMs.
    """
    vms = request.get("vms", [])

    if not vms:
        raise HTTPException(status_code=400, detail="Debe proporcionar al menos una VM")

    # Validación estricta para el endpoint custom, asumiendo que el usuario ya usa MB/GB
    for vm in vms:
        if "id" not in vm or "cpu" not in vm or "ram" not in vm or "storage" not in vm:
            raise HTTPException(
                status_code=400,
                detail="Cada VM debe tener: id, cpu (vCPUs), ram (MB), storage (GB)"
            )

    hosts = get_hosts()
    if not hosts:
        raise HTTPException(status_code=503, detail="No hay hosts disponibles")

    hosts = preprocess_hosts(hosts)

    try:
        # Validar que cada VM pueda caber en al menos un host
        for vm in vms:
            validate_single_vm_fits(vm, hosts)

        # Validar recursos totales
        validate_resources(vms, hosts)

    except ResourceValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    best = run_genetic_algorithm(vms, hosts)

    return build_placement_result(best, vms, hosts)

##OpenStack
# Asegúrese de que 'requests', 'logger', 'HTTPException', 'Header', 'Request', 'Optional'
# y todas las funciones I-GA (preprocess_hosts, fitness, run_genetic_algorithm,
# validate_resources, validate_single_vm_fits) estén definidas previamente.

# -----------------------------
# Nueva Función auxiliar para obtener hosts de OpenStack
# -----------------------------
def get_openstack_hosts():
    """
    Obtiene la lista de hosts disponibles desde la API de recursos (8003)
    usando el endpoint /hosts_openstacks y normaliza las unidades.
    (MODIFICADA para incluir carga base)
    """
    try:
        # CAMBIO DE ENDPOINT: Se consulta al recurso de OpenStack
        resp = requests.get("http://10.20.12.32:8003/hosts_openstacks", timeout=30)
        resp.raise_for_status()
        data = resp.json()

        hosts_data = data["hosts"] if isinstance(data, dict) and "hosts" in data else []

        normalized_hosts = []
        for h in hosts_data:
            # Se usan los valores power_idle/max del OVS2 (OpenStack)
            normalized_hosts.append({
                "id": h["id"],
                "ip": h["ip"],
                "cpu": h.get("cpu_total", 0),
                "ram": h.get("ram_total_mb", 0),
                "storage": h.get("storage_total_gb", 0),
                "availability": h.get("availability", 1.0),
                "power_idle": h.get("power_idle", 150.0),  # Valores esperados del OVS2
                "power_max": h.get("power_max", 300.0),  # Valores esperados del OVS2
                # 🆕 Carga Base Actual (Asumida del Monitoring/Prometheus):
                "cpu_used_cores": h.get("cpu_used_cores", 0),
                "ram_used_mb": h.get("ram_used_mb", 0),
                "storage_used_gb": h.get("storage_used_gb", 0)
            })

        logger.info(f"✓ Obtenidos {len(normalized_hosts)} hosts de OpenStack (API /hosts_openstacks) con carga base")
        return normalized_hosts
    except Exception as e:
        logger.error(f"✗ Error obteniendo hosts de OpenStack: {e}")
        return []


def build_openstack_placement_result(best_chromosome, vms, hosts, slice_id):
    """
    Construye el resultado del placement con formato OpenStack,
    mapeando el ID del host a 'availability_zone'.
    """
    placement = {h["id"]: [] for h in hosts}
    used = {h["id"]: {"cpu": 0, "ram": 0, "storage": 0} for h in hosts}

    for i, vm in enumerate(vms):
        h_id = hosts[best_chromosome[i]]["id"]
        placement[h_id].append(vm["id"])
        used[h_id]["cpu"] += vm["cpu"]
        used[h_id]["ram"] += vm["ram"]
        used[h_id]["storage"] += vm["storage"]

    usage_summary = []
    active_hosts_for_avail = []

    for h in hosts:
        # Calcular ratios y energía para el host
        cpu_ratio = used[h["id"]]["cpu"] / h["cpu_virtual"]

        # Calcular energía solo si hay VMs asignadas
        if cpu_ratio > 0:
            energy = energy_consumption(cpu_ratio, h)
            active_hosts_for_avail.append(h)
        else:
            energy = 0

        # Solo incluimos hosts que tienen asignaciones
        if placement[h["id"]]:
            usage_summary.append({
                "availability_zone": h["id"],  # Usamos el ID del host como AZ
                "cpu_usage": round(cpu_ratio, 3),
                "energy": round(energy, 2),
                "availability": h["availability"],
                "assigned_vms": placement[h["id"]]
            })

    # Recalcular totales con solo los hosts que fueron activados
    total_energy = sum(u["energy"] for u in usage_summary)
    total_avail = availability_product(active_hosts_for_avail) if active_hosts_for_avail else 0

    return {
        "platform": "openstack",
        "placements": usage_summary,
        "total_energy": round(total_energy, 2),
        "total_availability": round(total_avail, 4),
        "fitness_score": round(fitness(best_chromosome, vms, hosts), 4),
        "slice_id": slice_id,
        "total_vms": len(vms)
    }


# -----------------------------
# NUEVO ENDPOINT PARA OPENSTACK
# -----------------------------

@app.post("/placement/openstack/slice/{slice_id}")
async def get_openstack_slice_placement(
        slice_id: int,
        request: Request,
        authorization: Optional[str] = Header(None)
):
    """
    Calcula el placement óptimo para las VMs de un slice en el entorno OpenStack.
    Utiliza el Resource Manager en /hosts_openstacks y devuelve formato AZ.
    """
    try:
        logger.info(f"🔍 [Slice {slice_id} | OpenStack] Procesando solicitud de placement")

        # --- Obtener VMs (Se asume que la lógica de obtención es la misma) ---
        vms = []
        body = await request.json()
        vms_data = body.get("vms")

        if vms_data and len(vms_data) > 0:
            for vm in vms_data:
                vms.append({
                    "id": vm.get("name") or f"vm_{vm['id']}",
                    "vm_id": vm["id"],
                    "cpu": vm.get("cpu", 1),
                    "ram": vm.get("ram", 1024),
                    "storage": vm.get("disk", 3)
                })
        else:
            raise HTTPException(
                status_code=400,
                detail="Se requiere el array 'vms' en el cuerpo del request."
            )

        # --- Obtener HOSTS de OpenStack ---
        hosts = get_openstack_hosts()  # <-- Uso de la nueva función para OVS2

        if not hosts:
            raise HTTPException(
                status_code=503,
                detail="No hay hosts de OpenStack disponibles en el Resource Manager"
            )

        hosts = preprocess_hosts(hosts)
        logger.info(f"✓ Hosts de OpenStack preprocesados: {len(hosts)}")

        # --- Validación de Recursos ---
        try:
            for vm in vms:
                validate_single_vm_fits(vm, hosts)
            validate_resources(vms, hosts)
        except ResourceValidationError as e:
            logger.error(f"❌ Validación de recursos falló: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail=f"Recursos insuficientes para el slice {slice_id} en OpenStack: {str(e)}"
            )

        # --- Ejecutar I-GA ---
        logger.info(f"🧬 Ejecutando algoritmo I-GA en OpenStack con {len(vms)} VMs...")
        best = run_genetic_algorithm(vms, hosts)

        # --- Construir Resultado con formato OpenStack ---
        result = build_openstack_placement_result(best, vms, hosts, slice_id)

        logger.info(f"✅ Placement OpenStack completado exitosamente")
        return result

    except HTTPException:
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail=f"Error conectando con servicios (Resource Manager OpenStack): {str(e)}"
        )
    except Exception as e:
        logger.error(f"❌ Error inesperado: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error interno calculando placement para OpenStack: {str(e)}"
        )

@app.get("/health")
def health_check():
    """Verifica el estado del servicio"""
    try:
        hosts = get_hosts()
        return {
            "status": "healthy",
            "available_hosts": len(hosts),
            "algorithm": "I-GA",
            "version": "1.0",
            "services": {
                "resource_manager": "http://10.20.12.32:8003",
                "slice_manager": "http://10.20.12.32:8001"
            }
        }
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e)
        }