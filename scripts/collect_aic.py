"""Art Institute of Chicago API에서 퍼블릭 도메인 회화를 수집한다.

사용: python3 scripts/collect_aic.py [--limit N]
API 키 불필요. 이미지는 공식 IIIF 서버 URL로 저장한다.
"""
import argparse

from artdb import connect, get_json, upsert

BASE = "https://api.artic.edu/api/v1"
IIIF = "https://www.artic.edu/iiif/2"
FIELDS = ",".join([
    "id", "title", "artist_display", "artist_title", "date_display", "date_start", "date_end",
    "medium_display", "classification_title", "department_title", "place_of_origin",
    "style_title", "subject_titles", "image_id", "is_public_domain", "credit_line",
])
PAGE_SIZE = 100


def to_row(a: dict) -> dict | None:
    if not a.get("is_public_domain") or not a.get("image_id") or not a.get("title"):
        return None
    image_id = a["image_id"]
    return {
        "source": "aic",
        "source_id": str(a["id"]),
        "title": a["title"],
        "artist": a.get("artist_title") or None,
        "artist_bio": a.get("artist_display") or None,
        "date_display": a.get("date_display") or None,
        "year_start": a.get("date_start"),
        "year_end": a.get("date_end"),
        "medium": a.get("medium_display") or None,
        "classification": a.get("classification_title") or None,
        "department": a.get("department_title") or None,
        "origin": a.get("place_of_origin") or None,
        "style": a.get("style_title") or None,
        "subjects": ", ".join(a.get("subject_titles") or []) or None,
        "image_url": f"{IIIF}/{image_id}/full/1686,/0/default.jpg",
        "thumbnail_url": f"{IIIF}/{image_id}/full/400,/0/default.jpg",
        "source_url": f"https://www.artic.edu/artworks/{a['id']}",
        "credit_line": a.get("credit_line") or None,
        "is_public_domain": 1,
        "license": "CC0",
        "is_highlight": 0,
    }


# AIC 검색 API는 한 질의에서 1000건까지만 조회되므로(초과 시 403) 제작 시기 구간으로 나눠 수집한다.
WINDOWS = [(None, 1600)] + [(y, y + 50) for y in range(1600, 2000, 50)] + [(2000, None)]


def window_filters(lo, hi) -> dict:
    f = {
        "query[bool][must][0][term][is_public_domain]": "true",
        "query[bool][must][1][term][classification_titles.keyword]": "painting",
    }
    n = 2
    if lo is not None:
        f[f"query[bool][must][{n}][range][date_start][gte]"] = lo
        n += 1
    if hi is not None:
        f[f"query[bool][must][{n}][range][date_start][lt]"] = hi
    return f


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0이면 전체")
    args = parser.parse_args()

    conn = connect()
    saved = skipped = 0
    for lo, hi in WINDOWS:
        page = 1
        while True:
            data = get_json(f"{BASE}/artworks/search", {
                **window_filters(lo, hi), "fields": FIELDS, "limit": PAGE_SIZE, "page": page,
            })
            items = (data or {}).get("data") or []
            if not items:
                break
            for item in items:
                row = to_row(item)
                if row is None:
                    skipped += 1
                    continue
                upsert(conn, row)
                saved += 1
            conn.commit()
            pg = data["pagination"]
            print(f"  [{lo}~{hi}) page {page}/{pg['total_pages']} (구간 {pg['total']}건) 누적 {saved}")
            if pg["total"] > 1000:
                print("  경고: 구간이 1000건을 넘어 일부가 누락됩니다. WINDOWS를 더 잘게 나누세요.")
            if args.limit and saved >= args.limit:
                conn.close()
                print(f"완료(limit): 저장 {saved}, 제외 {skipped}")
                return
            if page >= pg["total_pages"]:
                break
            page += 1
    conn.close()
    print(f"완료: 저장 {saved}, 제외 {skipped}")


if __name__ == "__main__":
    main()
