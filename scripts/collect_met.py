"""MET Open Access에서 유럽 회화(부서 11) 중 퍼블릭 도메인 + 이미지 있는 작품을 수집한다.

사용: python3 scripts/collect_met.py [--limit N]
API 키 불필요. 공식 제한(초당 80회)보다 훨씬 낮게 동시 요청 수를 제한한다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

from artdb import connect, get_json, upsert

BASE = "https://collectionapi.metmuseum.org/public/collection/v1"
DEPARTMENT_ID = 11  # European Paintings


def to_row(o: dict) -> dict | None:
    if not o.get("isPublicDomain") or not o.get("primaryImage") or not o.get("title"):
        return None
    tags = [t["term"] for t in (o.get("tags") or []) if t.get("term")]
    return {
        "source": "met",
        "source_id": str(o["objectID"]),
        "title": o["title"],
        "artist": o.get("artistDisplayName") or None,
        "artist_bio": o.get("artistDisplayBio") or None,
        "date_display": o.get("objectDate") or None,
        "year_start": o.get("objectBeginDate"),
        "year_end": o.get("objectEndDate"),
        "medium": o.get("medium") or None,
        "classification": o.get("classification") or o.get("objectName") or None,
        "department": o.get("department") or None,
        "origin": o.get("culture") or o.get("artistNationality") or None,
        "style": o.get("period") or None,
        "subjects": ", ".join(tags) or None,
        "image_url": o["primaryImage"],
        "thumbnail_url": o.get("primaryImageSmall") or None,
        "source_url": o.get("objectURL") or f"https://www.metmuseum.org/art/collection/search/{o['objectID']}",
        "credit_line": o.get("creditLine") or None,
        "is_public_domain": 1,
        "license": "CC0",
        "is_highlight": int(bool(o.get("isHighlight"))),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0이면 전체")
    args = parser.parse_args()

    search = get_json(f"{BASE}/search", {
        "departmentId": DEPARTMENT_ID, "isPublicDomain": "true", "hasImages": "true", "q": "*",
    })
    ids = (search or {}).get("objectIDs") or []
    if args.limit:
        ids = ids[: args.limit]
    print(f"MET 대상 {len(ids)}건 수집 시작")

    conn = connect()
    saved = skipped = failed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for obj_id, obj in zip(ids, pool.map(lambda i: _fetch(i), ids)):
            if obj is None:
                failed += 1
                continue
            row = to_row(obj)
            if row is None:
                skipped += 1
                continue
            upsert(conn, row)
            saved += 1
            if saved % 200 == 0:
                conn.commit()
                print(f"  {saved}건 저장…")
    conn.commit()
    conn.close()
    print(f"완료: 저장 {saved}, 제외 {skipped}, 실패 {failed}")


def _fetch(obj_id: int):
    try:
        return get_json(f"{BASE}/objects/{obj_id}")
    except Exception as e:  # 개별 실패는 건너뛰고 계속 진행
        print(f"  실패 objectID={obj_id}: {e}")
        return None


if __name__ == "__main__":
    main()
