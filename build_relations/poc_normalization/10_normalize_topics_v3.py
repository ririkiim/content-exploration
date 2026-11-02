import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path


BASE = Path("data/enrichment/normalization")


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    return " ".join(text.split())


def compact(text):
    """공백과 구두점을 제거한 비교용 문자열."""
    return "".join(
        char for char in normalize(text)
        if char.isalnum()
    )


def unique(values):
    result = []
    seen = set()

    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)

        if key not in seen:
            seen.add(key)
            result.append(value)

    return result


def exact_label_qids(topic):
    """주제 이름과 Wikidata 레이블이 정확히 일치하는 QID."""
    name = normalize(topic["display_name"])
    result = set()

    for candidate in topic.get("qid_candidates", []):
        qid = candidate.get("qid")

        if not qid:
            continue

        labels = candidate.get("labels") or []

        if any(normalize(label) == name for label in labels):
            result.add(qid)

    return result


def same_qid_identity(a, b):
    """
    두 표현이 동일 QID의 정확한 레이블이고,
    서로 경쟁하는 정확한 레이블 QID가 없는 경우만 통합.
    """
    a_qids = exact_label_qids(a)
    b_qids = exact_label_qids(b)

    return (
        len(a_qids) == 1
        and len(b_qids) == 1
        and a_qids == b_qids
    )


class UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]

        return x

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)

        if ra != rb:
            self.parent[rb] = ra


def merge_reason(a, b):
    name_a = normalize(a["display_name"])
    name_b = normalize(b["display_name"])

    if name_a == name_b:
        return "exact_normalized_text"

    # 짧은 문자열의 우연한 일치 방지
    if (
        len(compact(name_a)) >= 5
        and compact(name_a) == compact(name_b)
    ):
        return "punctuation_or_whitespace_variant"

    if same_qid_identity(a, b):
        return "shared_unique_exact_label_qid"

    return None


def candidate_qids(topic):
    return {
        c["qid"]
        for c in topic.get("qid_candidates", [])
        if c.get("qid")
    }


def merge_group(content_id, group):
    names = unique(
        name
        for topic in group
        for name in topic.get(
            "name_variants", [topic["display_name"]]
        )
    )

    # 첫 번째 원본 표현을 대표 이름으로 유지
    display_name = group[0]["display_name"]

    original_ids = unique(
        topic_id
        for topic in group
        for topic_id in topic.get(
            "original_topic_ids", [topic["topic_id"]]
        )
    )

    qid_candidates = unique(
        candidate
        for topic in group
        for candidate in topic.get("qid_candidates", [])
    )

    qids = {
        c["qid"]
        for c in qid_candidates
        if c.get("qid")
    }

    if not qids:
        qid_status = "no_candidate"
    elif len(qids) == 1:
        qid_status = "single_candidate_unverified"
    else:
        qid_status = "ambiguous_candidates"

    identity = "|".join(sorted(original_ids))

    digest = hashlib.sha256(
        f"{content_id}:{identity}".encode()
    ).hexdigest()[:16]

    return {
        "topic_id": f"topic_candidate:{content_id}:{digest}",
        "display_name": display_name,
        "name_variants": names,
        "qid": None,
        "qid_candidates": qid_candidates,
        "qid_resolution_status": qid_status,
        "candidate_origins": unique(
            origin
            for topic in group
            for origin in topic.get("candidate_origins", [])
        ),
        "relation_claims": unique(
            claim
            for topic in group
            for claim in topic.get("relation_claims", [])
        ),
        "evidence_document_ids": unique(
            evidence_id
            for topic in group
            for evidence_id in topic.get("evidence_document_ids", [])
        ),
        "original_topic_ids": original_ids,
        "source_references": unique(
            reference
            for topic in group
            for reference in topic.get("source_references", [])
        ),
        "merge_count": len(original_ids),
        "evidence_status": "unchecked",
        "review_status": "proposed",
        "service_status": "not_selected",
    }


def process(content_id):
    source = BASE / f"topic_catalogue_{content_id}_v2.json"
    target = BASE / f"topic_catalogue_{content_id}_v3.json"

    if not source.exists():
        raise FileNotFoundError(source)

    # 기존 결과 덮어쓰기 방지
    if target.exists():
        raise FileExistsError(
            f"이미 존재하는 파일입니다: {target}"
        )

    with source.open(encoding="utf-8") as f:
        data = json.load(f)

    topics = data["topics"]
    uf = UnionFind(len(topics))

    merge_evidence = []

    for i in range(len(topics)):
        for j in range(i + 1, len(topics)):
            reason = merge_reason(topics[i], topics[j])

            if reason is None:
                continue

            # 이미 병합된 집합의 다른 QID가 충돌할 수 있으므로
            # 통합된 전체 집합의 정확한 레이블 QID를 확인
            members = [
                topics[k]
                for k in range(len(topics))
                if uf.find(k) in {
                    uf.find(i),
                    uf.find(j),
                }
            ]

            exact_qids = set().union(
                *(exact_label_qids(t) for t in members)
            )

            if len(exact_qids) > 1:
                continue

            uf.union(i, j)

            merge_evidence.append({
                "left": topics[i]["display_name"],
                "right": topics[j]["display_name"],
                "reason": reason,
                "shared_exact_label_qids": sorted(
                    exact_label_qids(topics[i])
                    & exact_label_qids(topics[j])
                ),
            })

    groups = defaultdict(list)

    for index, topic in enumerate(topics):
        groups[uf.find(index)].append(topic)

    normalized_topics = [
        merge_group(content_id, group)
        for group in groups.values()
    ]

    merged_groups = [
        {
            "display_name": topic["display_name"],
            "name_variants": topic["name_variants"],
            "merge_count": topic["merge_count"],
        }
        for topic in normalized_topics
        if topic["merge_count"] > 1
    ]

    output = {
        "schema_version": "topic_catalogue_v3",
        "pipeline_version": "topic_catalogue_v3",
        "content_id": content_id,
        "title": data.get("title"),
        "content_type": data.get("content_type"),
        "source_file": source.name,
        "topics": normalized_topics,
        "navigation_candidates": data.get(
            "navigation_candidates", []
        ),
        "unclassified_candidates": data.get(
            "unclassified_candidates", []
        ),
        "evidence_documents": data.get(
            "evidence_documents", []
        ),
        "merge_evidence": merge_evidence,
        "summary": {
            "input_topics": len(topics),
            "output_topics": len(normalized_topics),
            "removed_duplicates": (
                len(topics) - len(normalized_topics)
            ),
            "merged_groups": merged_groups,
            "qid_confirmed": 0,
        },
        "policy": {
            "llm_used": False,
            "database_written": False,
            "qid_auto_confirmed": False,
            "wikipedia_links_as_evidence": False,
        },
    }

    BASE.mkdir(parents=True, exist_ok=True)

    with target.open("x", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[{content_id}] {data.get('title')}")
    print(f"입력 주제: {len(topics)}")
    print(f"출력 주제: {len(normalized_topics)}")
    print(
        "통합된 중복:",
        len(topics) - len(normalized_topics)
    )

    for group in merged_groups:
        print("  통합:", " ↔ ".join(group["name_variants"]))

    print(f"저장: {target}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--content-id",
        type=int,
        required=True,
    )

    args = parser.parse_args()
    process(args.content_id)


if __name__ == "__main__":
    main()