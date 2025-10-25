from fastapi import APIRouter
from core.deployment_manager import deploy_slice

router = APIRouter()

@router.post("/slices/deploy/{slice_id}")
async def deploy(slice_id: int):
    result = deploy_slice(slice_id)
    return {"slice_id": slice_id, "result": result}
