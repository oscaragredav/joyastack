import json

from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from hashlib import sha256
import os
from utils.database import get_db
from utils.ssh import SSHConnection
from sqlalchemy import text
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from core.deployment_manager import deployment_slice

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


# ----------------------------------------------------
# POST: Crear un slice
# ----------------------------------------------------
@app.post("/slices/create")
async def create_slice(
        request: Request,
        payload: dict = Depends(verify_token),
        db: Session = Depends(get_db)
):
    """
    Crea un slice con sus VMs asociadas para el usuario autenticado.
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
        # Crear slice
        print(f"Creando slice '{name}' para usuario '{username}'")
        result_slice = db.execute(
            text("""
                INSERT INTO slice (name, owner_id, status, template)
                VALUES (:name, :owner_id, :status, :template)
            """),
            {"name": name, "owner_id": user_id, "status": "PENDIENTE", "template": json.dumps(template)}
        )
        db.commit()
        slice_id = result_slice.lastrowid
        print(f"Slice creado con ID {slice_id}")

        # Crear VMs asociadas
        for n in nodes:
            db.execute(
                text("""
                    INSERT INTO vm (name, slice_id, image_id, cpu, ram, disk, state)
                    VALUES (:name, :slice_id, :image_id, :cpu, :ram, :disk, :state)
                """),
                {
                    "name": n["label"],
                    "slice_id": slice_id,
                    "image_id": 1,
                    "cpu": n.get("cpu", 1),
                    "ram": n.get("ram", 256),
                    "disk": n.get("disk", 3),
                    "state": "PENDIENTE"
                }
            )
        db.commit()

        return {
            "slice_id": slice_id,
            "message": f"Slice {name} guardado (PENDIENTE)",
            "owner": username
        }

    except Exception as e:
        db.rollback()
        print(f"Error al crear slice: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al crear slice: {str(e)}")


# ----------------------------------------------------
# POST: Desplegar un slice
# ----------------------------------------------------
@app.post("/slices/deploy/{slice_id}")
async def deploy_slice(
        slice_id: int,
        payload: dict = Depends(verify_token),
        db: Session = Depends(get_db)
):
    """
    Despliega un slice verificando que pertenezca al usuario autenticado.
    """
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
        # Llamar a la función de despliegue
        result = deployment_slice(slice_id, db)

        return {
            "slice_id": slice_id,
            "result": result,
            "owner": username
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al desplegar slice: {str(e)}"
        )

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
        ip="10.20.12.154", port=5824, user="ubuntu", password="RedesCloud2025"
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
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        ssh_headnode.close()