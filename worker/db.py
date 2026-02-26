from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from config import settings

engine = create_engine(settings.database_url, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=engine)


def get_session() -> Session:
    return SessionLocal()
