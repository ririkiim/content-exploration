import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

INPUT_PATH = ROOT / "data/enrichment/normalization/normalized.json"
OUTPUT_PATH = ROOT / "data/enrichment/expansion/input.json"


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def prepare_expansion_input(data):
    items_by_id = {
        item["candidate_id"]: item
        for item in data["normalized_items"]
    }

    expansion_topics = []
    skipped = []

    for topic in data["topics"]:
        candidate_ids = topic["candidate_ids"]

        candidates = [
            items_by_id[candidate_id]
            for candidate_id in candidate_ids
        ]

        valid_candidates = [
            item
            for item in candidates
            if item.get("normalization_status") == "accepted"
            and item.get("eligible_for_review") is True
        ]

        if not valid_candidates:
            skipped.append({
                "canonical_name": topic["canonical_name"],
                "reason": "no_valid_candidates",
            })
            continue

        evidence_items = []
        all_flags = set()

        for item in valid_candidates:
            flags = item.get("review_flags") or []
            all_flags.update(flags)

            evidence_items.append({
                "candidate_id": item["candidate_id"],
                "content_id": item["content_id"],
                "original_name": item["original_name"],
                "type": item.get("type"),
                "evidence": item.get("evidence"),
                "evidence_quote": item.get("evidence_quote"),
                "evidence_source": item.get("evidence_source"),
                "evidence_level": item.get("evidence_level"),
                "confidence": item.get("confidence"),
                "review_flags": flags,
            })

        expansion_topics.append({
            "temporary_topic_id": topic["temporary_topic_id"],
            "canonical_name": topic["canonical_name"],
            "content_ids": sorted({
                item["content_id"]
                for item in valid_candidates
            }),
            "candidate_count": len(valid_candidates),
            "needs_review": bool(all_flags),
            "review_flags": sorted(all_flags),
            "evidence_items": evidence_items,
        })

    return expansion_topics, skipped


def main():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"입력 파일이 없습니다: {INPUT_PATH}"
        )

    if OUTPUT_PATH.exists():
        raise FileExistsError(
            f"이미 결과 파일이 존재합니다: {OUTPUT_PATH}\n"
            "기존 결과를 보호하기 위해 덮어쓰지 않습니다."
        )

    data = load_json(INPUT_PATH)

    expansion_topics, skipped = prepare_expansion_input(data)

    flagged_count = sum(
        topic["needs_review"]
        for topic in expansion_topics
    )

    result = {
        "created_at": datetime.now().astimezone().isoformat(),
        "source_file": str(INPUT_PATH.relative_to(ROOT)),
        "stage": "semantic_expansion_prepare",
        "summary": {
            "total_topics": len(data["topics"]),
            "prepared_topics": len(expansion_topics),
            "flagged_topics": flagged_count,
            "skipped_topics": len(skipped),
        },
        "topics": expansion_topics,
        "skipped": skipped,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n===== 관계 확장 입력 준비 완료 =====")
    print(f"전체 토픽: {len(data['topics'])}개")
    print(f"확장 준비: {len(expansion_topics)}개")
    print(f"검토 플래그 포함: {flagged_count}개")
    print(f"제외: {len(skipped)}개")
    print(f"저장: {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()