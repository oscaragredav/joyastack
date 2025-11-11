from fastapi import FastAPI, HTTPException
import random
import requests
from typing import List, Dict

app = FastAPI(title="Improved Genetic Algorithm for VM Placement", version="I-GA 1.0")


# -----------------------------
# Función auxiliar para obtener hosts
# -----------------------------
def get_hosts():
    """Obtiene la lista de hosts disponibles desde la API de recursos"""
    try:
        resp = requests.get("http://localhost:8001/hosts", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error obteniendo hosts: {e}")
        # Hosts de fallback para testing
        return [
            {"id": "host1", "cpu": 16, "ram": 32, "storage": 500, "availability": 0.99, "power_idle": 100,
             "power_max": 300},
            {"id": "host2", "cpu": 12, "ram": 24, "storage": 400, "availability": 0.95, "power_idle": 90,
             "power_max": 250},
            {"id": "host3", "cpu": 20, "ram": 48, "storage": 800, "availability": 0.98, "power_idle": 120,
             "power_max": 350}
        ]


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
# Funciones del algoritmo
# -----------------------------
def energy_consumption(usage_ratio, host):
    """Modelo simple de energía basado en el paper (ecuación 8)."""
    return host["power_idle"] + (host["power_max"] - host["power_idle"]) * (usage_ratio ** 3)


def availability_product(used_hosts):
    """Disponibilidad total multiplicada (ecuación 13 del paper)."""
    prod = 1.0
    for h in used_hosts:
        prod *= h["availability"]
    return prod


def preprocess_hosts(hosts):
    """Aplica VHAM (Virtual Host Availability Model)"""
    for h in hosts:
        h["cpu_virtual"] = h["cpu"] * CPU_OVER
        h["ram_virtual"] = h["ram"] * RAM_OVER
        h["storage_virtual"] = h["storage"] * STORAGE_OVER
        # Clustering virtual (VHAM): score ponderado
        h["vham_score"] = (
                0.6 * (h["cpu_virtual"] / max(h2["cpu_virtual"] for h2 in hosts))
                + 0.3 * h["availability"]
                - 0.1 * (h["power_max"] / max(h2["power_max"] for h2 in hosts))
        )
    hosts.sort(key=lambda x: x["vham_score"], reverse=True)
    return hosts


def fitness(chromosome, vms, hosts):
    """Calcula el fitness según ecuación (16)"""
    usage = {h["id"]: {"cpu": 0, "ram": 0, "storage": 0} for h in hosts}

    for i, vm in enumerate(vms):
        h = hosts[chromosome[i]]
        usage[h["id"]]["cpu"] += vm["cpu"]
        usage[h["id"]]["ram"] += vm["ram"]
        usage[h["id"]]["storage"] += vm["storage"]

    active_hosts = []
    total_energy = 0

    for h in hosts:
        cpu_ratio = usage[h["id"]]["cpu"] / h["cpu_virtual"]
        if cpu_ratio > 0:  # host activo
            active_hosts.append(h)
            total_energy += energy_consumption(cpu_ratio, h)

    if not active_hosts:
        return float("inf")

    availability = availability_product(active_hosts)
    E_min = min(h["power_idle"] for h in hosts)

    # Ecuación (16)
    G_T = 0.5 * ((E_min / total_energy) + availability)
    return 1 / G_T  # invertimos para minimizar


def create_chromosome(vms, hosts):
    """Inicialización guiada por VHAM"""
    chrom = []
    for vm in vms:
        # probabilidad proporcional al score VHAM
        probs = [h["vham_score"] for h in hosts]
        s = sum(probs)
        probs = [p / s for p in probs]
        host_idx = random.choices(range(len(hosts)), weights=probs, k=1)[0]
        chrom.append(host_idx)
    return chrom


def crossover(p1, p2, n_vms):
    """Crossover jerárquico (por clusters)"""
    cluster_size = n_vms // 2
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

    # Población inicial
    population = [create_chromosome(vms, hosts) for _ in range(POP_SIZE)]

    # Evolución principal
    for _ in range(GENERATIONS):
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

    # Mejor resultado
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
    vms = [
        {"id": "vm1", "cpu": 4, "ram": 8, "storage": 100},
        {"id": "vm2", "cpu": 6, "ram": 12, "storage": 80},
        {"id": "vm3", "cpu": 8, "ram": 16, "storage": 200},
        {"id": "vm4", "cpu": 3, "ram": 4, "storage": 50}
    ]
    hosts = get_hosts()
    hosts = preprocess_hosts(hosts)

    best = run_genetic_algorithm(vms, hosts)
    return build_placement_result(best, vms, hosts)


@app.post("/placement/slice/{slice_id}")
def get_slice_placement(slice_id: int):
    """
    Calcula el placement óptimo para las VMs de un slice específico.

    Parámetros:
    - slice_id: ID del slice a desplegar

    Retorna:
    - placements: Lista de hosts con las VMs asignadas
    - total_energy: Consumo energético total
    - total_availability: Disponibilidad agregada del sistema
    - fitness_score: Puntuación de calidad del placement
    """
    try:
        # Obtener las VMs del slice desde el slice manager
        resp = requests.get(f"http://localhost:8001/slices/{slice_id}", timeout=5)
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Slice {slice_id} no encontrado")
        resp.raise_for_status()

        slice_data = resp.json()

        # Extraer VMs del slice
        if "vms" not in slice_data or not slice_data["vms"]:
            raise HTTPException(status_code=400, detail="El slice no tiene VMs definidas")

        vms = []
        for vm in slice_data["vms"]:
            vms.append({
                "id": vm.get("name") or f"vm_{vm['id']}",
                "vm_id": vm["id"],  # ID original de la VM en la BD
                "cpu": vm.get("cpu", 1),
                "ram": vm.get("ram", 256),
                "storage": vm.get("disk", 3)
            })

        # Obtener hosts disponibles
        hosts = get_hosts()
        if not hosts:
            raise HTTPException(status_code=503, detail="No hay hosts disponibles")

        # Preprocesar hosts (VHAM)
        hosts = preprocess_hosts(hosts)

        # Ejecutar algoritmo genético
        best = run_genetic_algorithm(vms, hosts)

        # Construir resultado
        result = build_placement_result(best, vms, hosts)

        # Agregar información del slice
        result["slice_id"] = slice_id
        result["slice_name"] = slice_data.get("name", f"Slice-{slice_id}")
        result["total_vms"] = len(vms)

        return result

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503,
            detail=f"Error conectando con el slice manager: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error calculando placement: {str(e)}"
        )


@app.post("/placement/custom")
def get_custom_placement(request: Dict):
    """
    Calcula el placement óptimo para una lista personalizada de VMs.

    Body:
    {
        "vms": [
            {"id": "vm1", "cpu": 4, "ram": 8, "storage": 100},
            {"id": "vm2", "cpu": 2, "ram": 4, "storage": 50}
        ]
    }
    """
    vms = request.get("vms", [])

    if not vms:
        raise HTTPException(status_code=400, detail="Debe proporcionar al menos una VM")

    # Validar estructura de VMs
    for vm in vms:
        if "id" not in vm or "cpu" not in vm or "ram" not in vm or "storage" not in vm:
            raise HTTPException(
                status_code=400,
                detail="Cada VM debe tener: id, cpu, ram, storage"
            )

    hosts = get_hosts()
    if not hosts:
        raise HTTPException(status_code=503, detail="No hay hosts disponibles")

    hosts = preprocess_hosts(hosts)
    best = run_genetic_algorithm(vms, hosts)

    return build_placement_result(best, vms, hosts)


@app.get("/health")
def health_check():
    """Verifica el estado del servicio"""
    try:
        hosts = get_hosts()
        return {
            "status": "healthy",
            "available_hosts": len(hosts),
            "algorithm": "I-GA",
            "version": "1.0"
        }
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e)
        }