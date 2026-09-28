"""읽기 전용 미술 DB(data/art.db) 검색."""
import re
import sqlite3

from .config import ROOT

ART_DB = ROOT / "data" / "art.db"
CARD_FIELDS = (
    "a.id, a.source, a.title, a.artist, a.date_display, a.medium, a.image_url, "
    "a.thumbnail_url, a.source_url, a.license, a.credit_line, a.is_highlight"
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{ART_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fts_query(keywords: list[str]) -> str:
    """키워드를 안전한 FTS5 OR 질의로 바꾼다 (영문/숫자 토큰만, 각각 따옴표로 감쌈)."""
    tokens = []
    for kw in keywords:
        tokens += re.findall(r"[A-Za-z0-9]+", kw)
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(tokens))


def search(keywords: list[str], artist: str | None = None,
           year_from: int | None = None, year_to: int | None = None, limit: int = 6) -> list[dict]:
    where, params = ["a.is_public_domain = 1"], []
    if artist:
        where.append("a.artist LIKE ?")
        params.append(f"%{artist}%")
    if year_from is not None:
        where.append("a.year_end >= ?")
        params.append(year_from)
    if year_to is not None:
        where.append("a.year_start <= ?")
        params.append(year_to)

    fts = _fts_query(keywords)
    conn = _connect()
    try:
        if fts:
            sql = (f"SELECT {CARD_FIELDS} FROM artworks_fts f JOIN artworks a ON a.id = f.rowid "
                   f"WHERE artworks_fts MATCH ? AND {' AND '.join(where)} "
                   "ORDER BY a.is_highlight DESC, bm25(artworks_fts) LIMIT ?")
            rows = conn.execute(sql, [fts, *params, limit]).fetchall()
        else:
            sql = (f"SELECT {CARD_FIELDS} FROM artworks a WHERE {' AND '.join(where)} "
                   "ORDER BY a.is_highlight DESC, a.id LIMIT ?")
            rows = conn.execute(sql, [*params, limit]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
