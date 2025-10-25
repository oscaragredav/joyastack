from fastapi import FastAPI
from app.routers import slices, deployment

app = FastAPI(title="Joyastack Orchestrator")

app.include_router(slices.router)
app.include_router(deployment.router)
