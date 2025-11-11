from fastapi import FastAPI
import random
app = FastAPI(title="Improved Genetic Algorithm for VM Placement", version="I-GA 1.0")

@app.get("/placement")
def get_vm_placement():
    # -----------------------------
    # Datos de entrada
    # -----------------------------
    vms = [
        {"id": "vm1", "cpu": 4, "ram": 8, "storage": 100},
        {"id": "vm2", "cpu": 6, "ram": 12, "storage": 80},
        {"id": "vm3", "cpu": 8, "ram": 16, "storage": 200},
        {"id": "vm4", "cpu": 3, "ram": 4, "storage": 50}
    ]

    hosts = [
        {"id": "h1", "cpu": 10, "ram": 20, "storage": 200, "availability": 0.98, "power_idle": 100, "power_max": 250},
        {"id": "h2", "cpu": 12, "ram": 24, "storage": 250, "availability": 0.97, "power_idle": 120, "power_max": 270},
        {"id": "h3", "cpu": 16, "ram": 32, "storage": 300, "availability": 0.99, "power_idle": 140, "power_max": 300}
    ]

    # -----------------------------
    # Parámetros del sistema
    # -----------------------------
    CPU_OVER = 1.2
    RAM_OVER = 1.5
    STORAGE_OVER = 1.0

    # -----------------------------
    # Preprocesamiento (VHAM)
    # -----------------------------
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

    # -----------------------------
    # Parámetros del algoritmo I-GA
    # -----------------------------
    POP_SIZE = 50
    GENERATIONS = 100
    ELITE_SIZE = 5
    MUTATION_RATE = 0.2

    n_vms = len(vms)
    n_hosts = len(hosts)

    # -----------------------------
    # Función de energía y disponibilidad
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

    # -----------------------------
    # Fitness según ecuación (16)
    # -----------------------------
    def fitness(chromosome):
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

    # -----------------------------
    # Inicialización (VHAM-guided)
    # -----------------------------
    def create_chromosome():
        chrom = []
        for vm in vms:
            # probabilidad proporcional al score VHAM
            probs = [h["vham_score"] for h in hosts]
            s = sum(probs)
            probs = [p / s for p in probs]
            host_idx = random.choices(range(n_hosts), weights=probs, k=1)[0]
            chrom.append(host_idx)
        return chrom

    # -----------------------------
    # Crossover jerárquico (por clusters)
    # -----------------------------
    def crossover(p1, p2):
        cluster_size = n_vms // 2
        point = random.randint(0, cluster_size - 1)
        return p1[:point] + p2[point:]

    # -----------------------------
    # Mutación adaptativa (carga desequilibrada)
    # -----------------------------
    def mutate(chrom):
        for i in range(n_vms):
            if random.random() < MUTATION_RATE:
                host_idx = random.randint(0, n_hosts - 1)
                chrom[i] = host_idx
        return chrom

    # -----------------------------
    # Población inicial
    # -----------------------------
    population = [create_chromosome() for _ in range(POP_SIZE)]

    # -----------------------------
    # Evolución principal
    # -----------------------------
    for _ in range(GENERATIONS):
        scored = [(chrom, fitness(chrom)) for chrom in population]
        scored.sort(key=lambda x: x[1])
        elites = [x[0] for x in scored[:ELITE_SIZE]]

        new_population = elites.copy()
        while len(new_population) < POP_SIZE:
            p1, p2 = random.sample(elites, 2)
            child = crossover(p1, p2)
            child = mutate(child)
            new_population.append(child)
        population = new_population

    # -----------------------------
    # Mejor resultado
    # -----------------------------
    best = min(population, key=fitness)
    placement = {h["id"]: [] for h in hosts}
    used = {h["id"]: {"cpu": 0, "ram": 0, "storage": 0} for h in hosts}

    for i, vm in enumerate(vms):
        h_id = hosts[best[i]]["id"]
        placement[h_id].append(vm["id"])
        used[h_id]["cpu"] += vm["cpu"]
        used[h_id]["ram"] += vm["ram"]
        used[h_id]["storage"] += vm["storage"]

    usage_summary = []
    for h in hosts:
        cpu_ratio = used[h["id"]]["cpu"] / h["cpu_virtual"]
        energy = energy_consumption(cpu_ratio, h)
        usage_summary.append({
            "host_id": h["id"],
            "cpu_usage": round(cpu_ratio, 3),
            "energy": round(energy, 2),
            "availability": h["availability"],
            "assigned_vms": placement[h["id"]]
        })

    total_energy = sum(u["energy"] for u in usage_summary)
    total_avail = availability_product([h for h in hosts if used[h["id"]]["cpu"] > 0])

    return {
        "placements": usage_summary,
        "total_energy": round(total_energy, 2),
        "total_availability": round(total_avail, 4),
        "fitness_score": round(fitness(best), 4)
    }