"""09. QID 의미 검토 자료 생성.

입력:
    data/enrichment/normalization/evidence_bundle_{cid}_v3.json

출력:
    data/enrichment/normalization/qid_semantic_review_{cid}_v1.json

정책:
- DB/API/LLM 사용 없음
- 원본 후보 삭제 없음
- QID 자동 선택 없음
- 점수 및 임의 가중치 없음
- P31 유형별 차단 없음
- 콘텐츠별 예외 없음
- 기존 결과 덮어쓰기 없음
"""

import argparse
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("data/enrichment/normalization")


def load_json(path):
    if not path.exists():
        raise FileNotFoundError(f"입력 파일 없음: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize(value):
    """대소문자, 유니코드 표현, 공백 차이만 정규화한다.

    단어 내부 공백이나 구두점을 임의로 제거하지 않는다.
    """
    if not isinstance(value, str):
        return ""

    value = unicodedata.normalize("NFKC", value)
    return " ".join(value.casefold().split())


def is_qid(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"Q[1-9]\d*", value) is not None
    )


def as_strings(value):
    if not isinstance(value, list):
        return []

    return [
        item
        for item in value
        if isinstance(item, str) and item.strip()
    ]


def unique(values):
    result = []
    seen = set()

    for value in values:
        key = normalize(value)

        if not key or key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def prepare_qid_candidate(keyword, candidate):
    """후보 QID의 명칭과 의미 구분 자료를 보존한다."""
    qid = candidate.get("qid")

    labels = unique(as_strings(candidate.get("labels")))
    aliases = unique(as_strings(candidate.get("aliases")))
    descriptions = unique(
        as_strings(candidate.get("descriptions"))
    )

    normalized_keyword = normalize(keyword)

    label_match = any(
        normalize(label) == normalized_keyword
        for label in labels
    )

    alias_match = any(
        normalize(alias) == normalized_keyword
        for alias in aliases
    )

    instance_types = candidate.get("instance_of_types") or []
    instance_qids = candidate.get("instance_of_qids") or []

    return {
        "qid": qid,
        "labels": labels,
        "aliases": aliases,
        "descriptions": descriptions,
        "instance_of_qids": instance_qids,
        "instance_of_types": instance_types,

        "name_evidence": {
            "exact_label": label_match,
            "exact_alias": alias_match,
            "name_match": (
                "exact_label"
                if label_match
                else "exact_alias"
                if alias_match
                else "not_exact"
            ),
        },

        "metadata_completeness": {
            "has_label": bool(labels),
            "has_description": bool(descriptions),
            "has_instance_type": bool(
                instance_qids or instance_types
            ),
        },

        "semantic_decision": "undetermined",
        "semantic_review_reason": None,

        "original_candidate": candidate,
    }


def prepare_source_keyword(candidate, evidence_documents):
    """원본 키워드에 연결된 QID 후보를 의미 검토용으로 정리한다."""
    keyword = candidate.get("text", "")

    raw_candidates = candidate.get("qid_candidates") or []

    prepared = []
    seen_qids = set()

    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue

        qid = raw.get("qid")

        if not is_qid(qid):
            continue

        if qid in seen_qids:
            continue

        seen_qids.add(qid)

        prepared.append(
            prepare_qid_candidate(keyword, raw)
        )

    source_details = candidate.get("source_details") or []

    source_evidence = []
    wikipedia_evidence = []

    for detail in source_details:
        if not isinstance(detail, dict):
            continue

        source_evidence.extend(
            detail.get("source_evidence") or []
        )

        wikipedia_evidence.extend(
            detail.get("wikipedia_evidence") or []
        )

    # 콘텐츠 문맥은 검토 자료로 제공하지만,
    # 단순 문자열 출현만으로 의미 일치를 판단하지 않는다.
    context_documents = []

    for doc in evidence_documents:
        if not isinstance(doc, dict):
            continue

        context_documents.append({
            "evidence_id": doc.get("evidence_id"),
            "source": doc.get("source"),
            "source_kind": doc.get("source_kind"),
            "section": doc.get("section"),
            "text": doc.get("text"),
            "source_url": doc.get("source_url"),
            "revision_id": doc.get("revision_id"),
        })

    if not prepared:
        review_group = "no_qid_candidate"
    elif len(prepared) == 1:
        review_group = "single_qid_candidate"
    else:
        review_group = "multiple_qid_candidates"

    return {
        "keyword": keyword,
        "candidate_origin": "source_keyword",
        "relation_claim": candidate.get("relation_claim"),

        "review_group": review_group,
        "qid_candidate_count": len(prepared),

        "qid_candidates": prepared,

        "source_evidence": source_evidence,
        "wikipedia_evidence": wikipedia_evidence,

        "context_documents": context_documents,

        "selected_qid": None,
        "semantic_review_status": "pending",

        "limitations": [
            "후보 개수는 의미 일치의 신뢰도가 아님",
            "명칭 일치는 의미 일치를 보장하지 않음",
            "콘텐츠 문맥은 아직 자동 의미 판정에 사용되지 않음",
        ],
    }


def prepare_other_candidate(candidate):
    """원본 키워드가 아닌 후보는 별도 보존한다."""
    return {
        "text": candidate.get("text"),
        "candidate_origin": candidate.get("candidate_origin"),
        "relation_claim": candidate.get("relation_claim"),
        "qid_candidates": candidate.get("qid_candidates", []),
        "evidence_document_ids": candidate.get(
            "evidence_document_ids", []
        ),
        "source_details": candidate.get("source_details", []),
        "selected_qid": None,
        "semantic_review_status": "not_evaluated_in_step_09",
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

    input_path = BASE / f"evidence_bundle_{cid}_v3.json"
    output_path = BASE / f"qid_semantic_review_{cid}_v1.json"

    if output_path.exists():
        raise FileExistsError(
            f"이미 결과 파일이 존재함: {output_path}"
        )

    data = load_json(input_path)

    if data.get("content_id") != cid:
        raise ValueError("content_id 불일치")

    if data.get("schema_version") != "evidence_bundle_v3":
        raise ValueError("08번 v3 입력이 아님")

    candidates = data.get("candidates") or []
    evidence_documents = data.get("evidence_documents") or []

    source_keywords = []
    other_candidates = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        if candidate.get("candidate_origin") == "source_keyword":
            source_keywords.append(
                prepare_source_keyword(
                    candidate,
                    evidence_documents,
                )
            )
        else:
            other_candidates.append(
                prepare_other_candidate(candidate)
            )

    group_counts = Counter(
        row["review_group"]
        for row in source_keywords
    )

    total_qid_candidates = sum(
        row["qid_candidate_count"]
        for row in source_keywords
    )

    output = {
        "schema_version": "qid_semantic_review_v1",
        "content_id": cid,
        "title": data.get("title"),
        "content_type": data.get("content_type"),
        "created_at": datetime.now(timezone.utc).isoformat(),

        "source_keywords": source_keywords,

        "other_candidates": other_candidates,

        "summary": {
            "source_keywords": len(source_keywords),
            "single_qid_candidate": group_counts[
                "single_qid_candidate"
            ],
            "multiple_qid_candidates": group_counts[
                "multiple_qid_candidates"
            ],
            "no_qid_candidate": group_counts[
                "no_qid_candidate"
            ],
            "total_qid_candidates": total_qid_candidates,
            "other_candidates": len(other_candidates),
            "auto_selected_qids": 0,
        },

        "input_file": str(input_path),

        "policy": {
            "qid_selection": "none",
            "semantic_validation": "not_performed",
            "name_matching": "descriptive_only",
            "instance_type_filtering": False,
            "scoring": False,
            "database_write": False,
            "api_calls": False,
            "llm_calls": False,
        },

        "limitations": [
            "09번 v1은 의미 검증을 위한 자료 준비 단계임",
            "정확한 명칭 또는 별칭 일치는 의미 검증 결과가 아님",
            "P31 유형은 참고 정보이며 후보 제거 기준이 아님",
            "Wikipedia 링크 후보는 작품 관계가 검증되지 않음",
            "QID가 없는 주제는 이후 단계에서 별도로 처리해야 함",
            "콘텐츠와 후보 간 관계의 타당성은 판단하지 않음",
        ],
    }

    BASE.mkdir(parents=True, exist_ok=True)

    with output_path.open("x", encoding="utf-8") as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n콘텐츠: {output['title']} ({cid})")
    print(f"원본 키워드: {len(source_keywords)}")
    print(
        "QID 후보 1개:",
        group_counts["single_qid_candidate"],
    )
    print(
        "QID 후보 2개 이상:",
        group_counts["multiple_qid_candidates"],
    )
    print(
        "QID 후보 없음:",
        group_counts["no_qid_candidate"],
    )
    print(f"전체 QID 후보: {total_qid_candidates}")
    print(f"기타 출처 후보: {len(other_candidates)}")
    print("자동 선택: 0")
    print(f"\n저장: {output_path}")


if __name__ == "__main__":
    main()