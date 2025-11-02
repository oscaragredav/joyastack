from fastapi import FastAPI
from typing import List, Dict
#
# Este código define una API REST con FastAPI que expone un endpoint /placement,
# el cual ejecuta un algoritmo de colocación de máquinas virtuales (VMs) en hosts físicos,
# usando una versión multidimensional del algoritmo Bin Packing con balanceo de carga
# y overcommit.
#

app = FastAPI(title="Bin Packing API", version="1.0")

@app.get("/placement")
def get_vm_placement():
    # -----------------------------
    # -----------------------------
    # Datos hardcodeados
    # -----------------------------
    vms = [
        {"id": "vm1", "cpu": 4, "ram": 8, "storage": 100},
        {"id": "vm2", "cpu": 6, "ram": 12, "storage": 80},
        {"id": "vm3", "cpu": 8, "ram": 16, "storage": 200},
        {"id": "vm4", "cpu": 3, "ram": 4, "storage": 50}
    ]

    hosts = [
        {"id": "h1", "cpu": 10, "ram": 20, "storage": 200},
        {"id": "h2", "cpu": 12, "ram": 24, "storage": 250},
        {"id": "h3", "cpu": 16, "ram": 32, "storage": 300}
    ]

    # -----------------------------
    # Parámetros del sistema
    # -----------------------------
    W_CPU = 1.0
    W_RAM = 0.5
    W_STORAGE = 0.2
    W_BALANCE = 0.8

    # Overcommit ratios
    CPU_OVER = 1.2
    RAM_OVER = 1.5
    STORAGE_OVER = 1.0

    # Umbrales
    CPU_THRESHOLD = 0.85
    RAM_THRESHOLD = 0.90
    STORAGE_THRESHOLD = 0.95

    # -----------------------------
    # Preprocesamiento
    # -----------------------------
    for h in hosts:
        h["cpu_virtual"] = h["cpu"] * CPU_OVER
        h["ram_virtual"] = h["ram"] * RAM_OVER
        h["storage_virtual"] = h["storage"] * STORAGE_OVER
        h["used_cpu"] = 0
        h["used_ram"] = 0
        h["used_storage"] = 0

    # -----------------------------
    # 1. Ordenar VMs por demanda total
    # -----------------------------
    def total_demand(vm):
        return vm["cpu"] * W_CPU + vm["ram"] * W_RAM + vm["storage"] * W_STORAGE

    vms.sort(key=total_demand, reverse=True)
    placement = {h["id"]: [] for h in hosts}
    unassigned = []

    # -----------------------------
    # 2. Asignación principal
    # -----------------------------
    for vm in vms:
        best_host = None
        best_score = float("inf")

        for host in hosts:
            new_cpu_used = host["used_cpu"] + vm["cpu"]
            new_ram_used = host["used_ram"] + vm["ram"]
            new_storage_used = host["used_storage"] + vm["storage"]

            cpu_ratio = new_cpu_used / host["cpu_virtual"]
            ram_ratio = new_ram_used / host["ram_virtual"]
            storage_ratio = new_storage_used / host["storage_virtual"]

            if (
                cpu_ratio <= CPU_THRESHOLD and
                ram_ratio <= RAM_THRESHOLD and
                storage_ratio <= STORAGE_THRESHOLD
            ):
                cpu_left = host["cpu_virtual"] - new_cpu_used
                ram_left = host["ram_virtual"] - new_ram_used
                storage_left = host["storage_virtual"] - new_storage_used

                fit_score = cpu_left * W_CPU + ram_left * W_RAM + storage_left * W_STORAGE
                avg_usage = (cpu_ratio + ram_ratio + storage_ratio) / 3
                balance_score = abs(0.5 - avg_usage)

                total_score = fit_score + W_BALANCE * balance_score

                if total_score < best_score:
                    best_score = total_score
                    best_host = host

        if best_host:
            best_host["used_cpu"] += vm["cpu"]
            best_host["used_ram"] += vm["ram"]
            best_host["used_storage"] += vm["storage"]
            placement[best_host["id"]].append(vm["id"])
        else:
            unassigned.append(vm["id"])

    # -----------------------------
    # Resultados finales
    # -----------------------------
    usage_summary = []
    for h in hosts:
        cpu_ratio = h["used_cpu"] / h["cpu_virtual"]
        ram_ratio = h["used_ram"] / h["ram_virtual"]
        storage_ratio = h["used_storage"] / h["storage_virtual"]
        usage_summary.append({
            "host_id": h["id"],
            "cpu_usage": round(cpu_ratio, 3),
            "ram_usage": round(ram_ratio, 3),
            "storage_usage": round(storage_ratio, 3),
            "assigned_vms": placement[h["id"]]
        })

    return {
        "placements": usage_summary,
        "unassigned_vms": unassigned
    }
