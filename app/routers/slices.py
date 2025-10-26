from fastapi import APIRouter, Request
from sqlalchemy.orm import Session
from core.db_config import SessionLocal
from core.models import Slice, VM

router = APIRouter()

@router.post("/slices/create")
async def create_slice(request: Request):
    data = await request.json()
    name = data.get("name", "SliceDemo")
    nodes = data.get("nodes", [])

    db: Session = SessionLocal()
    try:
        slice_obj = Slice(name=name, owner_id=1, status="PENDIENTE") #actualizar owner_id despues
        db.add(slice_obj)
        db.commit()
        db.refresh(slice_obj)

        for n in nodes:
            vm = VM(
                name=n["label"],
                slice_id=slice_obj.id,
                image_id=1,
                cpu=n.get("cpu", 1),
                ram=n.get("ram", 256),
                disk=n.get("disk", 3),
                state="PENDIENTE"
            )
            db.add(vm)
        db.commit()

        return {"slice_id": slice_obj.id, "message": f"Slice {name} guardado (PENDIENTE)"}
    finally:
        db.close()
