import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from config.settings import DB_URL

load_dotenv()
# =======================================================
# CONFIGURACIÓN DE CONEXIÓN A MYSQL
# =======================================================



# Creamos el engine con pre_ping=True para reconexiones automáticas
engine = create_engine(DB_URL, pool_pre_ping=True)

# Session local para manejar transacciones y conexiones
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base para modelos ORM (si los defines después)
Base = declarative_base()


# =======================================================
# Dependencia para FastAPI (inyectar db en rutas)
# =======================================================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
