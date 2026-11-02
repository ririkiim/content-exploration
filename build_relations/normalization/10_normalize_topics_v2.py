"""10 v2 — Topic Catalogue 의미 정규화.

입력:
    topic_catalogue_{content_id}_v1.json

출력:
    topic_catalogue_{content_id}_v2.json

정책:
- DB/API/LLM 호출 없음
- 기존 파일 덮어쓰기 없음
- 작품별 하드코딩 없음
- QID 자동 확정 없음
- P31 기반 제외 없음
- 원본 주제와 출처 참조 보존
"""

import argparse
import hashlib
import json
import re
import unicodedata

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("data/enrichment/normalization_v2")

VERSION = "topic_catalogue_v2"


def load_json(path):
    if not path.exists():
        raise FileNotFoundError(f"입력 파일 없음: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize(text):
    if not isinstance(text, str):
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    return " ".join(text.split())


def make_id(content_id, names):
    """병합된 후보 그룹의 임시 ID."""
    keys = sorted(set(normalize(x) for x in names))

    raw = f"{content_id}:{'|'.join(keys)}"

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:16]

    return f"topic_candidate:{content_id}:{digest}"


def unique_strings(values):
    result = []
    seen = set()

    for value in values:
        if not isinstance(value, str):
            continue

        key = normalize(value)

        if not key or key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def candidate_qid_map(topic):
    """QID별 후보 메타데이터."""
    result = {}

    for candidate in topic.get("qid_candidates", []):
        if not isinstance(candidate, dict):
            continue

        qid = candidate.get("qid")

        if isinstance(qid, str) and qid:
            result.setdefault(qid, candidate)

    return result


def exact_name_for_qid(topic, qid):
    """주제 표현이 해당 QID의 레이블/별칭과 일치하는가?"""
    candidate = candidate_qid_map(topic).get(qid)

    if not candidate:
        return False

    names = (
        candidate.get("labels", [])
        + candidate.get("aliases", [])
    )

    names = {normalize(x) for x in names}

    topic_names = topic.get("name_variants", [])

    if not topic_names:
        topic_names = [topic.get("display_name", "")]

    return any(
        normalize(name) in names
        for name in topic_names
    )


def unique_qid(topic):
    """QID 후보가 정확히 하나일 때만 반환."""
    qids = list(candidate_qid_map(topic))

    if len(qids) == 1:
        return qids[0]

    return None


def same_identity(topic_a, topic_b):
    """서로 다른 표현의 보수적인 동일 개념 판단.

    조건:
    1. 양쪽 모두 QID 후보가 하나
    2. QID가 동일
    3. 양쪽 표현이 해당 QID의 레이블/별칭과 일치

    결과는 표현 통합 근거일 뿐 QID 확정이 아니다.
    """

    qid_a = unique_qid(topic_a)
    qid_b = unique_qid(topic_b)

    if not qid_a or qid_a != qid_b:
        return None

    if not exact_name_for_qid(topic_a, qid_a):
        return None

    if not exact_name_for_qid(topic_b, qid_b):
        return None

    return {
        "method": "shared_unique_qid_exact_name",
        "supporting_qid": qid_a,
        "verification_status": "candidate_identity_agreement",
    }


def source_keyword_ids(topic):
    """원본 키워드의 출처와 ID 추출.

    같은 숫자 ID라도 제공처가 다르면 다른 식별자로 취급한다.
    """

    result = set()

    for record in topic.get("source_records", []):
        for detail in record.get("source_details", []):
            for evidence in detail.get("source_evidence", []):
                if not isinstance(evidence, dict):
                    continue

                source = evidence.get("source")
                source_id = evidence.get("source_id")

                if source and source_id is not None:
                    result.add(
                        (str(source), str(source_id))
                    )

    return result


def same_source_identity(topic_a, topic_b):
    """동일한 원본 키워드 식별자 확인."""

    shared = (
        source_keyword_ids(topic_a)
        & source_keyword_ids(topic_b)
    )

    if not shared:
        return None

    return {
        "method": "same_source_keyword_id",
        "source_ids": [
            {
                "source": source,
                "source_id": source_id,
            }
            for source, source_id in sorted(shared)
        ],
        "verification_status": "source_identity_agreement",
    }


class UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]

        return x

    def union(self, a, b):
        root_a = self.find(a)
        root_b = self.find(b)

        if root_a != root_b:
            self.parent[root_b] = root_a


def build_merge_groups(topics):
    """근거가 있는 표현만 통합한다."""

    uf = UnionFind(len(topics))
    merge_evidence = []

    for i in range(len(topics)):
        for j in range(i + 1, len(topics)):
            a = topics[i]
            b = topics[j]

            evidence = None

            if normalize(a["display_name"]) == normalize(
                b["display_name"]
            ):
                evidence = {
                    "method": "exact_normalized_text",
                }

            if evidence is None:
                evidence = same_source_identity(a, b)

            if evidence is None:
                evidence = same_identity(a, b)

            if evidence is None:
                continue

            uf.union(i, j)

            merge_evidence.append({
                "left_topic_id": a["topic_id"],
                "right_topic_id": b["topic_id"],
                "left_name": a["display_name"],
                "right_name": b["display_name"],
                "evidence": evidence,
            })

    groups = defaultdict(list)

    for index, topic in enumerate(topics):
        root = uf.find(index)
        groups[root].append(topic)

    return list(groups.values()), merge_evidence


def choose_display_name(group):
    """대표 표기는 우선 원본 순서를 유지한다.

    번역이나 한국어 표기 선택은 별도 단계에서 검토한다.
    """
    return group[0]["display_name"]


def merge_group(content_id, group):
    names = []
    origins = []
    claims = []
    evidence_ids = []

    qid_map = {}

    original_topic_ids = []
    source_references = []

    for topic in group:
        original_topic_ids.append(topic["topic_id"])

        names.extend(
            topic.get(
                "name_variants",
                [topic["display_name"]],
            )
        )

        origins.extend(topic.get("candidate_origins", []))
        claims.extend(topic.get("relation_claims", []))

        evidence_ids.extend(
            topic.get("evidence_document_ids", [])
        )

        for qid, candidate in candidate_qid_map(topic).items():
            qid_map.setdefault(qid, candidate)

        source_references.append({
            "original_topic_id": topic["topic_id"],
            "display_name": topic["display_name"],
            "candidate_origins": topic.get(
                "candidate_origins", []
            ),
            "source_record_count": len(
                topic.get("source_records", [])
            ),
        })

    names = unique_strings(names)

    return {
        "topic_id": make_id(content_id, names),
        "display_name": choose_display_name(group),
        "name_variants": names,

        "qid": None,
        "qid_candidates": list(qid_map.values()),

        "qid_resolution_status": (
            "no_candidate"
            if len(qid_map) == 0
            else "single_candidate_unverified"
            if len(qid_map) == 1
            else "ambiguous_candidates"
        ),

        "candidate_origins": unique_strings(origins),
        "relation_claims": unique_strings(claims),

        "evidence_document_ids": unique_strings(
            evidence_ids
        ),

        "original_topic_ids": original_topic_ids,
        "source_references": source_references,

        "merge_count": len(group),

        "evidence_status": "unchecked",
        "review_status": "proposed",
        "service_status": "not_selected",
    }


def build_result(data):
    content_id = data["content_id"]

    topics = data.get("topics", [])

    groups, merge_evidence = build_merge_groups(topics)

    normalized_topics = [
        merge_group(content_id, group)
        for group in groups
    ]

    qid_status_counts = Counter(
        topic["qid_resolution_status"]
        for topic in normalized_topics
    )

    return {
        "schema_version": VERSION,
        "pipeline_version": VERSION,

        "content_id": content_id,
        "title": data.get("title"),
        "content_type": data.get("content_type"),

        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "topics": normalized_topics,

        "navigation_candidates": data.get(
            "navigation_candidates", []
        ),

        "merge_evidence": merge_evidence,

        "source_files": {
            "topic_catalogue_v1": (
                f"topic_catalogue_{content_id}_v1.json"
            ),
            "evidence_bundle_v3": (
                f"evidence_bundle_{content_id}_v3.json"
            ),
        },

        "summary": {
            "original_topic_count": len(topics),
            "normalized_topic_count": len(
                normalized_topics
            ),
            "reduced_topic_count": (
                len(topics) - len(normalized_topics)
            ),
            "merge_evidence_count": len(
                merge_evidence
            ),
            "qid_resolution": dict(
                qid_status_counts
            ),
            "navigation_candidate_count": len(
                data.get("navigation_candidates", [])
            ),
            "verified_topics": 0,
            "selected_topics": 0,
        },

        "policy": {
            "qid_auto_selection": False,
            "merge_methods": [
                "exact_normalized_text",
                "same_source_keyword_id",
                "shared_unique_qid_exact_name",
            ],
            "instance_type_filtering": False,
            "database_write": False,
            "api_calls": False,
            "llm_calls": False,
        },

        "limitations": [
            "QID 후보가 하나여도 자동 확정하지 않음",
            "공통 QID에 의한 통합은 후보 메타데이터에 의존함",
            "동일 QID 후보가 반드시 동일 의미임을 보장하지 않음",
            "P31 기반 후보 제외는 수행하지 않음",
            "표기 변형 중 외부 근거가 없는 것은 통합하지 않음",
            "작품과 주제의 관련성은 아직 검증되지 않음",
            "서비스용 주제 선정은 수행하지 않음",
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

    input_path = BASE / f"topic_catalogue_{cid}_v1.json"
    output_path = BASE / f"topic_catalogue_{cid}_v2.json"

    if output_path.exists():
        raise FileExistsError(
            f"기존 파일이 존재함: {output_path}"
        )

    data = load_json(input_path)

    if data.get("schema_version") != "topic_catalogue_v1":
        raise ValueError("10번 v1 입력이 아님")

    if data.get("content_id") != cid:
        raise ValueError("content_id 불일치")

    result = build_result(data)

    with output_path.open("x", encoding="utf-8") as f:
        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=2,
        )

    summary = result["summary"]

    print(f"\n콘텐츠: {result['title']} ({cid})")

    print(
        "기존 주제:",
        summary["original_topic_count"],
    )

    print(
        "정규화 후:",
        summary["normalized_topic_count"],
    )

    print(
        "통합된 주제 수:",
        summary["reduced_topic_count"],
    )

    print(
        "QID 상태:",
        summary["qid_resolution"],
    )

    print(
        "Wikipedia 탐색 후보:",
        summary["navigation_candidate_count"],
    )

    print(f"\n저장: {output_path}")

    if result["merge_evidence"]:
        print("\n통합 내역:")

        for item in result["merge_evidence"]:
            print(
                f"- {item['left_name']} "
                f"↔ {item['right_name']} "
                f"({item['evidence']['method']})"
            )


if __name__ == "__main__":
    main()