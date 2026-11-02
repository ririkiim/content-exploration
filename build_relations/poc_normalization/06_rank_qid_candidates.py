"""06. Wikidata QID 후보 정리 v2.

- 05번 결과만 입력으로 사용
- 동일 QID 중복 제거
- 후보의 라벨, 별칭, 설명, P31 보존
- 이름 일치 여부 기록
- 후보 제외 및 자동 선택 금지
- DB / API / LLM 사용 없음
"""

import argparse
import json
from pathlib import Path


BASE_DIR = Path("data/enrichment/normalization")


def read_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(value):
    if not isinstance(value, str):
        return ""
    return " ".join(value.casefold().split())


def unique_strings(values):
    result = []
    seen = set()

    for value in values or []:
        if not isinstance(value, str):
            continue

        key = normalize_text(value)

        if key and key not in seen:
            seen.add(key)
            result.append(value)

    return result


def merge_candidates(existing, incoming):
    """같은 QID의 정보를 합치되 기존 정보는 버리지 않는다."""

    for field in (
        "labels",
        "aliases",
        "descriptions",
        "instance_of_qids",
    ):
        existing[field] = unique_strings(
            existing.get(field, [])
            + incoming.get(field, [])
        )

    types = {
        item["qid"]: item
        for item in existing.get("instance_of_types", [])
        if isinstance(item, dict) and item.get("qid")
    }

    for item in incoming.get("instance_of_types", []):
        if not isinstance(item, dict):
            continue

        qid = item.get("qid")

        if not qid:
            continue

        if qid not in types:
            types[qid] = item
        else:
            for field in ("ko", "en"):
                if not types[qid].get(field) and item.get(field):
                    types[qid][field] = item[field]

    existing["instance_of_types"] = list(types.values())

    existing["exact_label"] = (
        existing.get("exact_label", False)
        or incoming.get("exact_label", False)
    )

    existing["exact_alias"] = (
        existing.get("exact_alias", False)
        or incoming.get("exact_alias", False)
    )

    return existing


def prepare_candidate(candidate, keyword):
    labels = unique_strings(candidate.get("labels", []))
    aliases = unique_strings(candidate.get("aliases", []))

    normalized_keyword = normalize_text(keyword)

    exact_label = normalized_keyword in {
        normalize_text(value) for value in labels
    }

    exact_alias = normalized_keyword in {
        normalize_text(value) for value in aliases
    }

    return {
        "qid": candidate["qid"],
        "labels": labels,
        "aliases": aliases,
        "descriptions": unique_strings(
            candidate.get("descriptions", [])
        ),
        "instance_of_qids": unique_strings(
            candidate.get("instance_of_qids", [])
        ),
        "instance_of_types": candidate.get(
            "instance_of_types", []
        ),
        "exact_label": exact_label,
        "exact_alias": exact_alias,
    }


def prepare_keyword(row):
    keyword = row["keyword"]

    candidates_by_qid = {}

    for candidate in row.get("candidates", []):
        if not isinstance(candidate, dict):
            continue

        qid = candidate.get("qid")

        if not isinstance(qid, str) or not qid:
            continue

        prepared = prepare_candidate(candidate, keyword)

        if qid in candidates_by_qid:
            merge_candidates(
                candidates_by_qid[qid],
                prepared,
            )
        else:
            candidates_by_qid[qid] = prepared

    candidates = list(candidates_by_qid.values())

    for candidate in candidates:
        if candidate["exact_label"]:
            match_type = "exact_label"
        elif candidate["exact_alias"]:
            match_type = "exact_alias"
        else:
            match_type = "other"

        candidate["name_match_type"] = match_type

    return {
        "keyword": keyword,
        "kind": row.get("kind"),
        "source_evidence": row.get("source_evidence", []),
        "source_match_status": row.get("status"),
        "source_matched_qid": row.get("matched_qid"),
        "source_match_reason": row.get("reason"),
        "wikipedia_evidence": row.get(
            "wikipedia_evidence", []
        ),
        "candidates": candidates,
        "candidate_count": len(candidates),
        "review_status": (
            "no_candidate"
            if not candidates
            else "pending_semantic_review"
        ),
        "selected_qid": None,
        "requires_review": True,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--content-id",
        type=int,
        required=True,
    )

    args = parser.parse_args()
    cid = args.content_id

    source_path = (
        BASE_DIR / f"keyword_qid_matches_{cid}_v4.json"
    )

    output_path = (
        BASE_DIR / f"keyword_qid_review_{cid}_v2.json"
    )

    source = read_json(source_path)

    if source.get("content_id") != cid:
        raise ValueError("content_id 불일치")

    rows = [
        prepare_keyword(row)
        for row in source.get("db_keywords", [])
    ]

    candidate_count = sum(
        row["candidate_count"] for row in rows
    )

    no_candidate_count = sum(
        row["candidate_count"] == 0 for row in rows
    )

    output = {
        "content_id": cid,
        "policy": "qid_candidate_preparation_v2",
        "source_file": str(source_path),
        "summary": {
            "total_db_keywords": len(rows),
            "keywords_with_candidates": (
                len(rows) - no_candidate_count
            ),
            "keywords_without_candidates": no_candidate_count,
            "total_candidates": candidate_count,
            "auto_approved": 0,
        },
        "db_keywords": rows,
        "other_source_tags": source.get(
            "other_source_tags", []
        ),
        "wikipedia_unmatched": source.get(
            "wikipedia_unmatched", []
        ),
        "limitations": [
            "후보의 의미 적합성을 판단하지 않음",
            "콘텐츠 맥락을 사용하지 않음",
            "P31에 따른 후보 제외를 수행하지 않음",
            "후보가 하나여도 자동 확정하지 않음",
            "05번에서 발견하지 못한 QID는 추가하지 않음",
        ],
    }

    BASE_DIR.mkdir(parents=True, exist_ok=True)

    with output_path.open("x", encoding="utf-8") as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n콘텐츠 ID: {cid}")
    print(f"원본 키워드: {len(rows)}개")
    print(f"후보가 있는 키워드: {len(rows) - no_candidate_count}개")
    print(f"후보가 없는 키워드: {no_candidate_count}개")
    print(f"전체 QID 후보: {candidate_count}개")
    print("자동 확정: 0개")
    print(f"\n저장: {output_path}")


if __name__ == "__main__":
    main()