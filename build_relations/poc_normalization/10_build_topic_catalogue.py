"""10 v1 — Evidence Bundle에서 주제 카탈로그 생성.

입력:
    data/enrichment/normalization/evidence_bundle_{cid}_v3.json

출력:
    data/enrichment/normalization/topic_catalogue_{cid}_v1.json

정책:
- DB/API/LLM 사용 없음
- 기존 파일 덮어쓰기 없음
- QID 자동 확정 없음
- 동일 표현만 중복 통합
- QID 없는 주제 유지
- Wikipedia 일반 링크는 별도 보관
- 모든 원본 근거 보존
"""

import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("data/enrichment/normalization")

PIPELINE_VERSION = "topic_catalogue_v1"

TOPIC_ORIGINS = {
    "source_keyword",
    "wikipedia_explicit_theme",
    "wikidata_subject",
}

NAVIGATION_ORIGINS = {
    "wikipedia_link",
}


def load_json(path):
    if not path.exists():
        raise FileNotFoundError(f"입력 파일 없음: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(value):
    """대소문자·유니코드·연속 공백만 정규화한다."""
    if not isinstance(value, str):
        return ""

    value = unicodedata.normalize("NFKC", value)
    return " ".join(value.casefold().split())


def make_local_id(content_id, text):
    """콘텐츠 범위의 임시 ID. 영구적인 의미 식별자가 아니다."""
    key = normalize_text(text)

    digest = hashlib.sha256(
        f"{content_id}:{key}".encode("utf-8")
    ).hexdigest()[:16]

    return f"topic_candidate:{content_id}:{digest}"


def unique_qid_candidates(candidates):
    """QID 후보를 중복 없이 보존한다. 자동 선택하지 않는다."""
    result = []
    seen = set()

    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue

        qid = candidate.get("qid")

        if not isinstance(qid, str) or not qid:
            continue

        if qid in seen:
            continue

        seen.add(qid)
        result.append(candidate)

    return result


def merge_qid_candidates(existing, incoming):
    """동일 QID의 메타데이터는 최초 항목을 유지한다.

    이후 항목에 추가 정보가 있으면 원본 provenance에 남는다.
    """
    return unique_qid_candidates(existing + incoming)


def build_topic_entry(content_id, candidate):
    text = candidate["text"].strip()

    return {
        "topic_id": make_local_id(content_id, text),
        "display_name": text,
        "normalized_name": normalize_text(text),

        "name_variants": [text],

        "qid": None,
        "qid_candidates": unique_qid_candidates(
            candidate.get("qid_candidates", [])
        ),

        "candidate_origins": [
            candidate.get("candidate_origin")
        ],

        "relation_claims": [
            candidate.get("relation_claim")
        ],

        "evidence_document_ids": list(
            dict.fromkeys(
                candidate.get("evidence_document_ids") or []
            )
        ),

        "source_records": [
            {
                "text": text,
                "candidate_origin": candidate.get(
                    "candidate_origin"
                ),
                "relation_claim": candidate.get(
                    "relation_claim"
                ),
                "source_details": candidate.get(
                    "source_details", []
                ),
                "qid_candidates": candidate.get(
                    "qid_candidates", []
                ),
                "evidence_document_ids": candidate.get(
                    "evidence_document_ids", []
                ),
            }
        ],

        "evidence_status": "unchecked",
        "review_status": "proposed",
        "service_status": "not_selected",

        "normalization_status": "exact_text_only",
    }


def merge_topic(entry, candidate):
    text = candidate["text"].strip()
    origin = candidate.get("candidate_origin")
    claim = candidate.get("relation_claim")

    if text not in entry["name_variants"]:
        entry["name_variants"].append(text)

    if origin not in entry["candidate_origins"]:
        entry["candidate_origins"].append(origin)

    if claim not in entry["relation_claims"]:
        entry["relation_claims"].append(claim)

    entry["qid_candidates"] = merge_qid_candidates(
        entry["qid_candidates"],
        candidate.get("qid_candidates", []),
    )

    for evidence_id in candidate.get("evidence_document_ids") or []:
        if evidence_id not in entry["evidence_document_ids"]:
            entry["evidence_document_ids"].append(evidence_id)

    entry["source_records"].append({
        "text": text,
        "candidate_origin": origin,
        "relation_claim": claim,
        "source_details": candidate.get("source_details", []),
        "qid_candidates": candidate.get("qid_candidates", []),
        "evidence_document_ids": candidate.get(
            "evidence_document_ids", []
        ),
    })


def build_catalogue(data):
    content_id = data["content_id"]

    topic_map = {}
    navigation_candidates = []
    unclassified_candidates = []

    for candidate in data.get("candidates", []):
        if not isinstance(candidate, dict):
            continue

        text = candidate.get("text")
        origin = candidate.get("candidate_origin")

        if not isinstance(text, str) or not text.strip():
            unclassified_candidates.append(candidate)
            continue

        if origin in TOPIC_ORIGINS:
            key = normalize_text(text)

            if key not in topic_map:
                topic_map[key] = build_topic_entry(
                    content_id,
                    candidate,
                )
            else:
                merge_topic(
                    topic_map[key],
                    candidate,
                )

        elif origin in NAVIGATION_ORIGINS:
            navigation_candidates.append({
                "text": text,
                "candidate_origin": origin,
                "relation_claim": candidate.get(
                    "relation_claim"
                ),
                "qid_candidates": candidate.get(
                    "qid_candidates", []
                ),
                "source_details": candidate.get(
                    "source_details", []
                ),
                "evidence_document_ids": candidate.get(
                    "evidence_document_ids", []
                ),
                "review_status": "discovery_only",
            })

        else:
            unclassified_candidates.append(candidate)

    topics = list(topic_map.values())

    origin_counts = Counter()

    for topic in topics:
        for origin in topic["candidate_origins"]:
            origin_counts[origin] += 1

    return {
        "schema_version": PIPELINE_VERSION,
        "pipeline_version": PIPELINE_VERSION,

        "content_id": content_id,
        "title": data.get("title"),
        "content_type": data.get("content_type"),

        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "topics": topics,
        "navigation_candidates": navigation_candidates,
        "unclassified_candidates": unclassified_candidates,

        "evidence_documents": data.get(
            "evidence_documents", []
        ),

        "summary": {
            "topic_count": len(topics),
            "topics_by_origin": dict(origin_counts),
            "topics_with_qid_candidates": sum(
                bool(topic["qid_candidates"])
                for topic in topics
            ),
            "topics_without_qid_candidates": sum(
                not topic["qid_candidates"]
                for topic in topics
            ),
            "navigation_candidate_count": len(
                navigation_candidates
            ),
            "unclassified_candidate_count": len(
                unclassified_candidates
            ),
            "verified_topics": 0,
            "selected_topics": 0,
        },

        "policy": {
            "qid_auto_selection": False,
            "semantic_merging": False,
            "exact_text_merging": True,
            "wikipedia_links_as_topics": False,
            "database_write": False,
            "api_calls": False,
            "llm_calls": False,
        },

        "limitations": [
            "동일 표현만 병합하며 동의어 통합은 수행하지 않음",
            "QID 후보는 식별자 후보이며 확정된 QID가 아님",
            "주제의 작품 관련성은 아직 검증되지 않음",
            "Wikipedia 링크는 주제 관계의 증거가 아님",
            "내부 topic_id는 콘텐츠 범위의 임시 식별자임",
            "LLM 추출 결과는 아직 통합하지 않음",
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
    output_path = BASE / f"topic_catalogue_{cid}_v1.json"

    if output_path.exists():
        raise FileExistsError(
            f"기존 결과 파일이 존재함: {output_path}"
        )

    data = load_json(input_path)

    if data.get("schema_version") != "evidence_bundle_v3":
        raise ValueError("08 Evidence Bundle v3 입력이 아님")

    if data.get("content_id") != cid:
        raise ValueError("content_id 불일치")

    result = build_catalogue(data)

    BASE.mkdir(parents=True, exist_ok=True)

    with output_path.open("x", encoding="utf-8") as f:
        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=2,
        )

    summary = result["summary"]

    print(f"\n콘텐츠: {result['title']} ({cid})")
    print(f"주제 후보: {summary['topic_count']}")
    print(f"출처별: {summary['topics_by_origin']}")
    print(
        "QID 후보 있는 주제:",
        summary["topics_with_qid_candidates"],
    )
    print(
        "QID 후보 없는 주제:",
        summary["topics_without_qid_candidates"],
    )
    print(
        "Wikipedia 탐색 후보:",
        summary["navigation_candidate_count"],
    )
    print(f"검증 완료: {summary['verified_topics']}")
    print(f"서비스 선정: {summary['selected_topics']}")
    print(f"저장: {output_path}")


if __name__ == "__main__":
    main()