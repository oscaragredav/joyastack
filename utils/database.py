import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()
# =======================================================
# CONFIGURACIÓN DE CONEXIÓN A MYSQL (AWS RDS)
# =======================================================

DB_USER = os.getenv("MYSQL_USER")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD")
DB_HOST = os.getenv("MYSQL_HOST")
DB_PORT = "3306"
DB_NAME = os.getenv("MYSQL_DB")

# Construimos la URL de conexión compatible con SQLAlchemy + PyMySQL
DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# Creamos el engine con pre_ping=True para reconexiones automáticas
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

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
