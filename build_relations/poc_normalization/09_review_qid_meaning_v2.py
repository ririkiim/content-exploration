"""09 v2 — QID 의미 검토.

입력:
    evidence_bundle_{content_id}_v3.json

출력:
    qid_semantic_review_{content_id}_v2.json

정책:
- DB/API/LLM 사용 없음
- QID 자동 확정 없음
- 후보 삭제 없음
- 임의 점수 없음
- P31 허용/차단 목록 없음
- 작품별 예외 없음
- 기존 파일 덮어쓰기 없음

의미 판정:
- meaning_mismatch: 일반 개념을 나타내는 키워드와
  특정 개별 항목을 가리키는 QID 사이의 명확한 불일치
- needs_review: 추가 검토 필요
- identity_provided_by_source: 출처가 QID를 직접 제공함
  (의미 및 작품 관계 검증 완료를 뜻하지 않음)
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
    if not isinstance(value, str):
        return ""

    value = unicodedata.normalize("NFKC", value)
    return " ".join(value.casefold().split())


def unique_strings(values):
    result = []
    seen = set()

    if not isinstance(values, list):
        return result

    for value in values:
        if not isinstance(value, str):
            continue

        key = normalize(value)

        if not key or key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def valid_qid(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"Q[1-9]\d*", value) is not None
    )


def extract_type_labels(candidate):
    result = []

    for item in candidate.get("instance_of_types") or []:
        if isinstance(item, dict):
            for field in ("ko", "en"):
                value = item.get(field)

                if isinstance(value, str) and value.strip():
                    result.append(value)

    return unique_strings(result)


def name_match(text, candidate):
    """명칭 일치는 참고 정보이며 의미 확정 근거가 아니다."""
    key = normalize(text)

    labels = unique_strings(candidate.get("labels"))
    aliases = unique_strings(candidate.get("aliases"))

    if any(normalize(x) == key for x in labels):
        return "exact_label"

    if any(normalize(x) == key for x in aliases):
        return "exact_alias"

    return "not_exact"


def extract_direct_identity(candidate):
    """출처가 직접 제공한 QID인지 확인한다."""
    origin = candidate.get("candidate_origin")
    details = candidate.get("source_details") or []

    if origin == "wikidata_subject":
        for detail in details:
            if not isinstance(detail, dict):
                continue

            if (
                detail.get("source") == "wikidata"
                and detail.get("property_id") == "P921"
                and valid_qid(detail.get("target_qid"))
            ):
                return {
                    "kind": "wikidata_statement",
                    "qid": detail["target_qid"],
                    "property_id": "P921",
                    "source_url": detail.get("source_url"),
                }

    if origin == "wikipedia_link":
        for detail in details:
            if not isinstance(detail, dict):
                continue

            if (
                detail.get("source") == "wikipedia"
                and valid_qid(detail.get("qid"))
            ):
                return {
                    "kind": "wikipedia_link",
                    "qid": detail["qid"],
                    "verification_role": "candidate_discovery_only",
                }

    return None


def assess_candidate(text, origin, qid_candidate, direct_identity):
    """후보별 의미 검토.

    외부 지식 없이 확인 가능한 출처 연결 및 명칭 정보를 사용한다.
    설명/P31의 자유 텍스트만으로 의미 불일치를 확정하지 않는다.
    """

    qid = qid_candidate.get("qid")

    labels = unique_strings(qid_candidate.get("labels"))
    aliases = unique_strings(qid_candidate.get("aliases"))
    descriptions = unique_strings(
        qid_candidate.get("descriptions")
    )

    match_type = name_match(text, qid_candidate)
    type_labels = extract_type_labels(qid_candidate)

    reasons = []

    if direct_identity and direct_identity.get("qid") == qid:
        decision = "identity_provided_by_source"

        if origin == "wikidata_subject":
            reasons.append(
                "Wikidata P921 문장에서 대상 QID가 직접 제공됨"
            )

        elif origin == "wikipedia_link":
            reasons.append(
                "Wikipedia 링크의 대상 QID가 직접 제공됨"
            )
            reasons.append(
                "링크 존재는 작품과의 의미 관계를 검증하지 않음"
            )

    else:
        decision = "needs_review"

        if match_type == "exact_label":
            reasons.append(
                "키워드와 Wikidata 레이블이 정확히 일치함"
            )

        elif match_type == "exact_alias":
            reasons.append(
                "키워드와 Wikidata 별칭이 정확히 일치함"
            )

        else:
            reasons.append(
                "키워드와 Wikidata 명칭의 정확한 일치가 확인되지 않음"
            )

        reasons.append(
            "명칭 일치만으로 의미 일치를 확정할 수 없음"
        )

    return {
        "qid": qid,
        "labels": labels,
        "aliases": aliases,
        "descriptions": descriptions,
        "instance_of_qids": qid_candidate.get(
            "instance_of_qids", []
        ),
        "instance_of_types": qid_candidate.get(
            "instance_of_types", []
        ),
        "type_labels": type_labels,

        "name_match": match_type,
        "decision": decision,
        "reasons": reasons,

        "source_identity": (
            direct_identity
            if direct_identity
            and direct_identity.get("qid") == qid
            else None
        ),

        "original_candidate": qid_candidate,
    }


def prepare_candidate(candidate, evidence_index):
    text = candidate.get("text", "")
    origin = candidate.get("candidate_origin")
    relation_claim = candidate.get("relation_claim")

    raw_qids = candidate.get("qid_candidates") or []
    direct_identity = extract_direct_identity(candidate)

    reviewed_qids = []
    seen_qids = set()

    for raw in raw_qids:
        if not isinstance(raw, dict):
            continue

        qid = raw.get("qid")

        if not valid_qid(qid):
            continue

        if qid in seen_qids:
            continue

        seen_qids.add(qid)

        reviewed_qids.append(
            assess_candidate(
                text=text,
                origin=origin,
                qid_candidate=raw,
                direct_identity=direct_identity,
            )
        )

    evidence_ids = candidate.get("evidence_document_ids") or []

    evidence_documents = [
        evidence_index[eid]
        for eid in evidence_ids
        if eid in evidence_index
    ]

    if not reviewed_qids:
        status = "no_qid_candidate"

    elif len(reviewed_qids) == 1:
        status = "single_qid_candidate"

    else:
        status = "multiple_qid_candidates"

    return {
        "text": text,
        "candidate_origin": origin,
        "relation_claim": relation_claim,

        "review_group": status,
        "qid_candidate_count": len(reviewed_qids),
        "qid_candidates": reviewed_qids,

        "source_details": candidate.get(
            "source_details", []
        ),

        "evidence_document_ids": evidence_ids,
        "evidence_documents": evidence_documents,

        "selected_qid": None,
        "semantic_review_status": "partially_assessed",

        "relationship_review_status": "not_evaluated",

        "limitations": [
            "명칭 일치만으로 의미를 확정하지 않음",
            "출처가 제공한 QID는 개념 식별 정보이지 관계 검증 결과가 아님",
        ],
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
    output_path = BASE / f"qid_semantic_review_{cid}_v2.json"

    if output_path.exists():
        raise FileExistsError(
            f"이미 결과 파일이 존재함: {output_path}"
        )

    data = load_json(input_path)

    if data.get("content_id") != cid:
        raise ValueError("content_id 불일치")

    if data.get("schema_version") != "evidence_bundle_v3":
        raise ValueError("08번 v3 입력이 아님")

    raw_candidates = data.get("candidates") or []
    raw_documents = data.get("evidence_documents") or []

    evidence_index = {
        doc["evidence_id"]: doc
        for doc in raw_documents
        if isinstance(doc, dict)
        and isinstance(doc.get("evidence_id"), str)
    }

    reviewed = []

    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue

        reviewed.append(
            prepare_candidate(candidate, evidence_index)
        )

    origin_counts = Counter(
        item["candidate_origin"]
        for item in reviewed
    )

    review_counts = Counter(
        item["review_group"]
        for item in reviewed
    )

    decision_counts = Counter(
        qid["decision"]
        for item in reviewed
        for qid in item["qid_candidates"]
    )

    output = {
        "schema_version": "qid_semantic_review_v2",
        "content_id": cid,
        "title": data.get("title"),
        "content_type": data.get("content_type"),
        "created_at": datetime.now(timezone.utc).isoformat(),

        "candidates": reviewed,

        "summary": {
            "total_candidates": len(reviewed),
            "by_origin": dict(origin_counts),
            "by_review_group": dict(review_counts),
            "total_qid_candidates": sum(
                item["qid_candidate_count"]
                for item in reviewed
            ),
            "by_semantic_decision": dict(decision_counts),
            "auto_selected_qids": 0,
        },

        "input_file": str(input_path),

        "policy": {
            "qid_selection": "none",
            "meaning_mismatch_detection": (
                "conservative_metadata_only"
            ),
            "semantic_validation": "partial",
            "instance_type_blocklist": False,
            "instance_type_allowlist": False,
            "scoring": False,
            "database_write": False,
            "api_calls": False,
            "llm_calls": False,
        },

        "limitations": [
            "09번 v2는 출처 기반 QID 식별과 의미 검토 자료를 통합함",
            "자유 텍스트 설명/P31만으로 의미 불일치를 자동 확정하지 않음",
            "Wikidata P921은 원천의 명시적 관계이며 사실 검증 완료를 뜻하지 않음",
            "Wikipedia 링크는 후보 발견 정보이며 관계 검증이 아님",
            "QID가 없는 후보도 보존함",
            "작품과 주제의 관계 검증은 다음 단계에서 수행함",
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
    print(f"전체 후보: {len(reviewed)}")
    print(f"출처별: {dict(origin_counts)}")
    print(f"QID 후보 없음: {review_counts['no_qid_candidate']}")
    print(f"전체 QID 후보: {output['summary']['total_qid_candidates']}")
    print(f"의미 검토: {dict(decision_counts)}")
    print("자동 확정: 0")
    print(f"\n저장: {output_path}")


if __name__ == "__main__":
    main()