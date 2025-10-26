from fastapi import FastAPI
from app.routers import slices, deployment
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI(title="Joyastack Orchestrator")

app.include_router(slices.router)
app.include_router(deployment.router)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

print(f"[INFO] Mounted static dir: {STATIC_DIR}")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
