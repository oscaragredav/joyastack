from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
import jwt
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from passlib.context import CryptContext
from utils.database import get_db
from sqlalchemy import text
from typing import Optional
import hashlib
import os
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

# ----------------------------------------------------------
# CONFIGURACIÓN
# ----------------------------------------------------------
load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "R3d3sCl0udJ0Y4St4cK")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Auth API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Frontend URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------
def verify_password(plain_password, hashed_password):
    """Verifica contraseña hash con bcrypt (si se usa)"""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Genera token JWT con expiración configurable"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ----------------------------------------------------------
# ENDPOINT LOGIN
# ----------------------------------------------------------
@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    Endpoint: /login
    Recibe username y password,
    devuelve access_token JWT si las credenciales son correctas.
    """
    user = db.execute(
        text("SELECT * FROM user WHERE username = :username"),
        {"username": form_data.username},
    ).mappings().first()

    if not user:
        raise HTTPException(status_code=400, detail="Usuario no encontrado")

    # Se compara password ingresado (SHA256) con el hash guardado en MySQL
    entered_hash = hashlib.sha256(form_data.password.encode()).hexdigest()
    if entered_hash != user["hash_password"]:
        raise HTTPException(status_code=401, detail="Contraseña incorrecta")

    token_data = {"sub": user["username"], "role": user["role"]}
    token = create_access_token(token_data)
    print(token)

    return {"access_token": token, "token_type": "bearer", "role": user["role"]}