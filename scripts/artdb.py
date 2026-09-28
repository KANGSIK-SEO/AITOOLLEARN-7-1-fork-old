"""미술 DB 공용 유틸: DB 연결/스키마 초기화, upsert, HTTP GET(재시도)."""
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "art.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"

COLUMNS = (
    "source", "source_id", "title", "artist", "artist_bio", "date_display",
    "year_start", "year_end", "medium", "classification", "department", "origin",
    "style", "subjects", "image_url", "thumbnail_url", "source_url", "credit_line",
    "is_public_domain", "license", "is_highlight",
)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def upsert(conn: sqlite3.Connection, row: dict) -> None:
    placeholders = ", ".join("?" for _ in COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c not in ("source", "source_id"))
    conn.execute(
        f"INSERT INTO artworks ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(source, source_id) DO UPDATE SET {updates}",
        [row.get(c) for c in COLUMNS],
    )


def get_json(url: str, params: dict | None = None, retries: int = 4, timeout: int = 30):
    """GET 후 JSON 반환. 404는 None, 그 외 실패는 지수 백오프로 재시도."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers={"User-Agent": "AITOOLLEARN-7-1 art-db-collector"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)
