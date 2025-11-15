import json
from datetime import datetime

from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from jose import JWTError, jwt
from hashlib import sha256
import os

from config.settings import WORKER_IPS, GATEWAY, SSH_USER, SSH_PASS
from utils.database import get_db
from utils.ssh import SSHConnection
from sqlalchemy import text
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from core.deployment_manager import deploy_slice

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM")

app = FastAPI(title="Joyastack Data API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Frontend URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def log_entry(db, module, level, message, slice_id):
    db.execute(
        text("""
            INSERT INTO logs (module, timestamp, level, message, slice_id)
            VALUES (:m, :ts, :lvl, :msg, :sid)
        """),
        {
            "m": module,
            "ts": datetime.utcnow(),
            "lvl": level,
            "msg": message,
            "sid": slice_id
        },
    )
    db.commit()


# ----------------------------------------------------
# Dependencia para validarToken
# ----------------------------------------------------
def verify_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=403, detail="Token no proporcionado")

    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido")

    return payload

# ----------------------------------------------------
# GET: todos los slices de un usuario + VM
# ----------------------------------------------------
@app.get("/slices")
def get_user_slices(payload: dict = Depends(verify_token), db: Session = Depends(get_db)):
    username = payload["sub"]
    user = db.execute(text("SELECT id FROM user WHERE username = :u"), {"u": username}).mappings().first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user_id = user["id"]

    result = db.execute(
        text("""
            SELECT 
                s.id AS slice_id, s.name AS slice_name, s.status, s.created_at, s.template,
                GROUP_CONCAT(DISTINCT v.id) AS vms
            FROM slice s
            LEFT JOIN vm v ON v.slice_id = s.id
            WHERE s.owner_id = :uid
            GROUP BY s.id;
        """),
        {"uid": user_id},
    ).mappings().all()

    return {"user": username, "slices": [dict(r) for r in result]}
#GET OBTENER SLICES POR ID
@app.get("/slices/{slice_id}")
def get_slice_by_id(slice_id: int, payload: dict = Depends(verify_token), db: Session = Depends(get_db)):
    import time
    import logging
    logger = logging.getLogger(__name__)

    start = time.time()
    logger.info(f"⏱️ Inicio get_slice_by_id: {slice_id}")

    username = payload["sub"]
    logger.info(f"⏱️ Token verificado ({time.time() - start:.2f}s)")

    user = db.execute(
        text("SELECT id FROM user WHERE username = :u"),
        {"u": username}
    ).mappings().first()
    logger.info(f"⏱️ Usuario obtenido ({time.time() - start:.2f}s)")

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user_id = user["id"]

    # Obtener el slice
    slice_data = db.execute(
        text("""
            SELECT id, name, status, created_at, template, owner_id
            FROM slice 
            WHERE id = :sid AND owner_id = :uid
        """),
        {"sid": slice_id, "uid": user_id}
    ).mappings().first()

    if not slice_data:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    # Obtener las VMs del slice
    vms = db.execute(
        text("""
            SELECT id, name, cpu, ram, disk, image_id, state, worker_id
            FROM vm 
            WHERE slice_id = :sid
        """),
        {"sid": slice_id}
    ).mappings().all()

    logger.info(f"⏱️ VMs obtenidas ({time.time() - start:.2f}s)")

    result = {
        "id": slice_data["id"],
        "name": slice_data["name"],
        "status": slice_data["status"],
        "created_at": str(slice_data["created_at"]),
        "template": slice_data["template"],
        "vms": [dict(vm) for vm in vms]
    }

    logger.info(f"⏱️ Total completado en: {time.time() - start:.2f}s")
    return result
# ----------------------------------------------------
# POST: Crear un slice
# ----------------------------------------------------
@app.post("/slices/create")
async def create_slice(
        request: Request,
        payload: dict = Depends(verify_token),
        db: Session = Depends(get_db)
):
    data = await request.json()
    name = data.get("name", "SliceDemo")
    nodes = data.get("nodes", [])
    links = data.get("links", [])

    # Crear el template completo
    template = {
        "nodes": nodes,
        "links": links
    }

    username = payload["sub"]
    user = db.execute(
        text("SELECT id FROM user WHERE username = :u"),
        {"u": username}
    ).mappings().first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user_id = user["id"]

    try:
        # Crear slice
        result_slice = db.execute(
            text("""
                INSERT INTO slice (name, owner_id, status, template)
                VALUES (:name, :owner_id, :status, :template)
            """),
            {
                "name": name,
                "owner_id": user_id,
                "status": "PENDIENTE",
                "template": json.dumps(template)
            }
        )
        db.commit()
        slice_id = result_slice.lastrowid

        # Mapeo de label a vm_id para crear links después
        vm_label_to_id = {}

        # Contar interfaces por VM (cuántos links tiene cada nodo)
        interface_count = {}
        for link in links:
            from_vm = link["from_vm"]
            to_vm = link["to_vm"]
            interface_count[from_vm] = interface_count.get(from_vm, 0) + 1
            interface_count[to_vm] = interface_count.get(to_vm, 0) + 1

        # Crear VMs con el número correcto de interfaces
        for n in nodes:
            num_ifaces = interface_count.get(n["label"], 1)  # Mínimo 1 interfaz

            result_vm = db.execute(
                text("""
                    INSERT INTO vm (name, slice_id, image_id, cpu, ram, disk, state, num_interfaces)
                    VALUES (:name, :slice_id, :image_id, :cpu, :ram, :disk, :state, :num_ifaces)
                """),
                {
                    "name": n["label"],
                    "slice_id": slice_id,
                    "image_id": n.get("image_id", 1),
                    "cpu": n.get("cpu", 1),
                    "ram": n.get("ram", 256),
                    "disk": n.get("disk", 3),
                    "state": "PENDIENTE",
                    "num_ifaces": num_ifaces
                }
            )
            vm_label_to_id[n["label"]] = result_vm.lastrowid

        db.commit()

        # Crear network_links con VLAN_IDs únicos
        vlan_id = 100
        for link in links:
            from_label = link["from_vm"]
            to_label = link["to_vm"]

            vm_a = vm_label_to_id.get(from_label)
            vm_b = vm_label_to_id.get(to_label)

            if vm_a and vm_b:
                db.execute(
                    text("""
                        INSERT INTO network_link (slice_id, vlan_id, vm_a, vm_b)
                        VALUES (:slice_id, :vlan_id, :vm_a, :vm_b)
                    """),
                    {
                        "slice_id": slice_id,
                        "vlan_id": vlan_id,
                        "vm_a": vm_a,
                        "vm_b": vm_b
                    }
                )
                vlan_id += 100  # Incrementar VLAN para el siguiente link

        db.commit()

        return {
            "slice_id": slice_id,
            "message": f"Slice {name} guardado (PENDIENTE)",
            "owner": username,
            "links_created": len(links)
        }

    except Exception as e:
        print(f"Error creando slice: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al crear slice: {str(e)}")


# ----------------------------------------------------
# POST: Desplegar un slice
# ----------------------------------------------------

@app.post("/slices/deploy/{slice_id}")
async def validate_deploy_slice(
        slice_id: int,
        payload: dict = Depends(verify_token),
        db: Session = Depends(get_db),
        authorization: str = Header(None)  # <-- capturamos el header Authorization
):
    """
    Despliega un slice verificando que pertenezca al usuario autenticado.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Token no proporcionado")

    token = authorization.split(" ")[1]  # quitar "Bearer "

    # Obtener usuario desde el token
    username = payload["sub"]
    user = db.execute(
        text("SELECT id FROM user WHERE username = :u"),
        {"u": username}
    ).mappings().first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user_id = user["id"]

    # Verificar que el slice pertenezca al usuario
    slice_obj = db.execute(
        text("SELECT id, owner_id, status FROM slice WHERE id = :sid"),
        {"sid": slice_id}
    ).mappings().first()

    if not slice_obj:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    if slice_obj["owner_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail="No tienes permiso para desplegar este slice"
        )

    try:
        # Pasar el token a deploy_slice
        result = deploy_slice(slice_id, db, token)

        return {
            "slice_id": slice_id,
            "result": result,
            "owner": username
        }

    except Exception as e:
        print(f"Error creando slice: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error al desplegar slice: {str(e)}"
        )

# =====================================================================
# UPDATE /slices/{slice_id}  → modificar slice
# =====================================================================


@app.post("/slices/update/{slice_id}")
async def update_slice(
    slice_id: int,
    request: Request,
    payload: dict = Depends(verify_token),
    db: Session = Depends(get_db)
):
    """
    Modifica un slice con sus VMs asociadas para el usuario autenticado.
    """
    data = await request.json()
    name = data.get("name", "SliceDemo")
    nodes = data.get("nodes", [])
    links = data.get("links", [])

    template = {
        "nodes": nodes,
        "links": links
    }

    # Obtener usuario desde el token
    username = payload["sub"]
    user = db.execute(
        text("SELECT id FROM user WHERE username = :u"),
        {"u": username}
    ).mappings().first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user_id = user["id"]

    try:
        # Verificar existencia del slice y que pertenezca al usuario
        slice_obj = db.execute(
            text("SELECT * FROM slice WHERE id = :sid AND owner_id = :uid"),
            {"sid": slice_id, "uid": user_id}
        ).mappings().first()

        if not slice_obj:
            raise HTTPException(status_code=404, detail="Slice no encontrado o no pertenece al usuario")

        # Actualizar datos del slice
        db.execute(
            text("""
                UPDATE slice
                SET name = :name,
                    template = :template,
                    status = :status
                WHERE id = :sid
            """),
            {
                "name": name,
                "template": json.dumps(template),
                "status": "PENDIENTE",
                "sid": slice_id
            }
        )

        # Eliminar VMs antiguas y crear las nuevas (puedes mejorar esto con un diff si quieres)
        db.execute(
            text("DELETE FROM vm WHERE slice_id = :sid"),
            {"sid": slice_id}
        )

        for n in nodes:
            db.execute(
                text("""
                    INSERT INTO vm (name, slice_id, image_id, cpu, ram, disk, state)
                    VALUES (:name, :slice_id, :image_id, :cpu, :ram, :disk, :state)
                """),
                {
                    "name": n["label"],
                    "slice_id": slice_id,
                    "image_id": n.get("image_id", 1),
                    "cpu": n.get("cpu", 1),
                    "ram": n.get("ram", 256),
                    "disk": n.get("disk", 3),
                    "state": "PENDIENTE"
                }
            )

        db.commit()

        log_entry(db, "SliceManager", "INFO", f"Slice {slice_id} actualizado correctamente", slice_id)

        return {
            "status": "updated",
            "slice_id": slice_id,
            "message": f"Slice '{name}' actualizado correctamente"
        }

    except Exception as e:
        print(f"Error creando slice: {e}")
        db.rollback()
        log_entry(db, "SliceManager", "ERROR", f"Error actualizando slice {slice_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al actualizar slice: {str(e)}")


# =====================================================================
# DELETE /slices/{slice_id}  → eliminar slice completo
# =====================================================================
@app.delete("/slices/delete/{slice_id}")
def delete_slice(slice_id: int, token=Depends(verify_token), db: Session = Depends(get_db)):
    """
    Borra un slice completo:
    - Elimina las VMs asociadas (vía SSH).
    - Limpia OvS, interfaces TAP, y entradas de BD.
    - Registra logs del proceso.
    """
    try:
        # Obtener VMs del slice
        vms = db.execute(
            text("""
                SELECT v.id, v.cpu, v.ram, v.disk, w.ip as worker_ip, w.id as worker_id
                FROM vm v
                JOIN worker w ON v.worker_id = w.id
                WHERE v.slice_id = :sid
            """),
            {"sid": slice_id},
        ).mappings().all()

        if not vms:
            return {"status": "not_found", "message": "No hay VMs en este slice"}

        log_entry(db, "SliceManager", "INFO", f"Eliminando slice {slice_id} con {len(vms)} VMs")

        for vm in vms:
            wip = vm["worker_ip"]
            ssh_port = None
            for name, data in WORKER_IPS.items():
                if data["ip"] == wip:
                    ssh_port = data["ssh_port"]
                    break
            if not ssh_port:
                continue

            conn = SSHConnection(GATEWAY, ssh_port, SSH_USER, SSH_PASS)
            if conn.connect():
                try:
                    # Matar proceso QEMU si existiese
                    conn.exec_sudo(f"pkill -f 'qemu-system.*VM_Auto_' || true")
                    conn.exec_sudo(f"sleep 1")
                    # Limpiar TAPs y OvS
                    conn.exec_sudo(
                        f"ovs-vsctl list-ports br-int | grep VM_Auto_ | xargs -r -I{{}} ovs-vsctl del-port br-int {{}}")
                    conn.exec_sudo(f"ip link del $(ip link show | grep VM_Auto_ | cut -d: -f2) 2>/dev/null || true")
                    log_entry(db, "SliceManager", "INFO", f"Limpieza de VM en worker {wip} completada")
                except Exception as e:
                    print(f"Error creando slice: {e}")
                    log_entry(db, "SliceManager", "ERROR", f"Error limpiando worker {wip}: {e}")
                finally:
                    conn.close()

        # Borrar en la base de datos (orden correcto debido a FKs)
        db.execute(text("DELETE FROM network_link WHERE slice_id = :sid"), {"sid": slice_id})
        db.execute(text("DELETE FROM vm WHERE slice_id = :sid"), {"sid": slice_id})
        db.execute(text("DELETE FROM logs WHERE module in ('WorkManager','SliceManager') AND message LIKE :sid_match"),
                   {"sid_match": f"%{slice_id}%"})
        db.execute(text("DELETE FROM slice WHERE id = :sid"), {"sid": slice_id})
        db.commit()

        log_entry(db, "SliceManager", "INFO", f"Slice {slice_id} eliminado correctamente")
        return {"status": "deleted", "slice_id": slice_id}

    except Exception as e:
        print(f"Error creando slice: {e}")
        db.rollback()
        log_entry(db, "SliceManager", "ERROR", f"Error eliminando slice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------------------------------
# GET: todos los flavor
# ----------------------------------------------------
@app.get("/flavors")
def get_templates(payload: dict = Depends(verify_token), db: Session = Depends(get_db)):
    result = db.execute(text("SELECT * FROM flavor")).mappings().all()
    return {"templates": [dict(r) for r in result]}


# ----------------------------------------------------
# GET: todos las imagenes
# ----------------------------------------------------
@app.get("/images")
def get_images(payload: dict = Depends(verify_token), db: Session = Depends(get_db)):
    result = db.execute(text("SELECT * FROM image")).mappings().all()
    return {"images": [dict(r) for r in result]}


# ============================================================
# POST /upload-image
# ============================================================
@app.post("/images/upload")
def upload_image(
        file: UploadFile = File(...),
        token_data: dict = Depends(verify_token),
        db: Session = Depends(get_db),
):
    """
    Sube una imagen a HeadNode vía SSH, la registra en la base de datos
    y reemplaza si ya existe una con el mismo nombre y tamaño.
    """

    # --- Cálculo de hash y tamaño local ---
    file_bytes = file.file.read()
    file_size = len(file_bytes)
    file_hash = sha256(file_bytes).hexdigest()
    filename = file.filename

    remote_path = f"/home/ubuntu/images/{filename}"

    # --- Comprobamos existencia previa (nombre+size) ---
    existing = db.execute(
        text(
            "SELECT * FROM image WHERE name=:name AND size=:size"
        ),
        {"name": filename, "size": file_size},
    ).mappings().first()

    # Datos SSH del HeadNode (vía gateway)
    ssh_headnode = SSHConnection(
        host="10.20.12.154", port=5821, user="ubuntu", password="RedesCloud2025"
    )

    if not ssh_headnode.connect():
        raise HTTPException(status_code=500, detail="No se pudo conectar al HeadNode")

    try:
        sftp = ssh_headnode.client.open_sftp()

        # Si existe imagen previa => eliminar remoto y registro
        if existing:
            print(f"✳️  Reemplazando imagen existente: {filename}")
            try:
                sftp.remove(remote_path)
            except Exception:
                pass
            db.execute(
                text("DELETE FROM image WHERE id=:id"), {"id": existing["id"]}
            )
            db.commit()

        # Asegurar carpeta remota
        try:
            sftp.chdir("/home/ubuntu/images")
        except IOError:
            ssh_headnode.exec_sudo("mkdir -p /home/ubuntu/images")
            ssh_headnode.exec_sudo("chown ubuntu:ubuntu /home/ubuntu/images")

        # Subir imagen
        tmp_local = f"/tmp/{filename}"
        with open(tmp_local, "wb") as f:
            f.write(file_bytes)

        sftp.put(tmp_local, remote_path)
        os.remove(tmp_local)
        sftp.close()

        # Registrar en BD
        result = db.execute(
            text(
                """
                INSERT INTO image (name, path, hash, size, reference_count)
                VALUES (:name, :path, :hash, :size, :ref)
                """
            ),
            {
                "name": filename,
                "path": remote_path,
                "hash": file_hash,
                "size": file_size,
                "ref": 0,
            },
        )
        db.commit()

        new_id = result.lastrowid

        return {
            "id": new_id,
            "name": filename,
            "path": remote_path,
            "size": file_size,
            "hash": file_hash,
            "status": "uploaded",
        }

    except Exception as e:
        print(f"Error creando slice: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        ssh_headnode.close()
