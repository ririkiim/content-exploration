import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

WIKI_DIR = ROOT / "data/enrichment/wikipedia"
OUTPUT_PATH = (
    ROOT
    / "data/enrichment/normalization/wikidata_candidates.json"
)

CONTENT_IDS = [42844, 79246, 84900]

ALLOWED_PROPERTIES = {
    "P921": "main_subject",
}


def collect_candidates(content_id):
    path = WIKI_DIR / f"context_{content_id}.json"

    if not path.exists():
        print(f"[SKIP] {content_id}: 위키 캐시 없음")
        return []

    data = json.loads(path.read_text(encoding="utf-8"))

    wikidata = data.get("wikidata") or {}
    claims = wikidata.get("claims") or []

    candidates = []

    for claim in claims:
        property_id = claim.get("property_id")

        if property_id not in ALLOWED_PROPERTIES:
            continue

        target_qid = claim.get("target_qid")
        target_label = claim.get("target_label")

        if not target_qid or not target_label:
            continue

        candidates.append(
            {
                "content_id": content_id,
                "content_title": data.get("content", {}).get("title"),
                "candidate_name": target_label,
                "wikidata_qid": target_qid,
                "property_id": property_id,
                "property_name": ALLOWED_PROPERTIES[property_id],
                "source": "wikidata",
                "source_work_qid": wikidata.get("qid"),
                "source_url": claim.get("source_url"),
                "status": "candidate",
                "entity_id": None,
            }
        )

    return candidates


def main():
    all_candidates = []

    for content_id in CONTENT_IDS:
        candidates = collect_candidates(content_id)
        all_candidates.extend(candidates)

        print(
            f"{content_id}: "
            f"Wikidata 주제 후보 {len(candidates)}개"
        )

    output = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_priority": [
            "existing_database",
            "wikidata_supplement",
        ],
        "note": (
            "Wikidata 후보는 기존 DB 키워드와 비교하기 전까지 "
            "Entity로 확정하지 않는다."
        ),
        "total_candidates": len(all_candidates),
        "candidates": all_candidates,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    if OUTPUT_PATH.exists():
        raise FileExistsError(
            f"이미 파일이 존재합니다: {OUTPUT_PATH}"
        )

    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n총 후보: {len(all_candidates)}개")
    print(f"저장: {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()