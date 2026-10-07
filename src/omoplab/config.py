import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def load_settings() -> dict[str, str]:
    load_dotenv(ROOT / ".env")
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit(".env에 POSTGRES_PASSWORD가 없습니다. .env.example을 복사해 만드세요.")
    return {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": os.environ.get("PGPORT", "15432"),
        "dbname": os.environ.get("PGDATABASE", "omop"),
        "user": os.environ.get("PGUSER", "omop"),
        "password": password,
        "schema": os.environ.get("CDM_SCHEMA", "cdm"),
    }
