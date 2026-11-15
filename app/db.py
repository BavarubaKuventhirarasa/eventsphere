import os
import time

os.environ.setdefault("TZ", "Asia/Colombo")  # event times are local (Sri Lanka)
if hasattr(time, "tzset"):
    time.tzset()

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

url = os.getenv("DATABASE_URL", "sqlite:///./eventsphere.db")
if url.startswith("postgres://"):  # Render gives postgres://, SQLAlchemy wants postgresql://
    url = url.replace("postgres://", "postgresql://", 1)
kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
engine = create_engine(url, **kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()
