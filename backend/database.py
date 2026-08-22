import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base   

# Reads a local .env file if present (see setup note below) - harmless if
# the file doesn't exist, it just does nothing in that case.
load_dotenv()

# Read from an environment variable instead of hardcoding real credentials in
# source. The fallback below is a local-dev placeholder only - it will NOT
# match your real Postgres password, so set DATABASE_URL in a .env file
# (see setup instructions).
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/saas_db"
)
engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()