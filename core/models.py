from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.sql import func
from core.db_config import Base

class Slice(Base):
    __tablename__ = "slice"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    status = Column(String(20), nullable=False, default="PENDIENTE")
    created_at = Column(DateTime, server_default=func.now())

class VM(Base):
    __tablename__ = "vm"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    slice_id = Column(Integer, ForeignKey("slice.id"), nullable=False)
    worker_id = Column(Integer, ForeignKey("worker.id"), nullable=True)
    image_id = Column(Integer, ForeignKey("image.id"), nullable=False)
    cpu = Column(Integer, nullable=False)
    ram = Column(Integer, nullable=False)
    disk = Column(Integer, nullable=False)
    state = Column(String(20), nullable=False, default="PENDIENTE")
    pid = Column(Integer, nullable=True)
    external_ssh_port = Column(Integer, nullable=True)

class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), nullable=False, unique=True)
    hash_password = Column(String(255), nullable=False)
    role = Column(String(20), default="admin")

class Worker(Base):
    __tablename__ = "worker"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    ip = Column(String(50), nullable=False)
    ssh_port = Column(Integer, nullable=False)
    state = Column(String(20), default="ACTIVE")
    capacity_cpu = Column(Integer, nullable=False)
    ram_total = Column(Integer, nullable=False)

class Image(Base):
    __tablename__ = "image"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    path = Column(String(255), nullable=False)
    has = Column(String(64), nullable=True)
    size = Column(Integer, nullable=True)
    reference_count = Column(Integer, default=0)
