from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from app.database.connection import Base, engine
from app.models.user import User

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost/resumen_ai")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

print("Creating database...")
Base.metadata.create_all(bind=engine)
