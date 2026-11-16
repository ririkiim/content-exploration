
import csv
import json
import random
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "exploration_v3/staging"
OUT = STAGING / "entity"
CACHE = ROOT / "data/enrichment/normalization_v2/wikidata_cache"

STATUS_PATH = OUT / "02_keyword_match_status.csv"
CANDIDATE_PATH = OUT / "03_wikidata_candidates.csv"
MAP_PATH = STAGING / "02_keyword_term_map.csv"

REL_PROPS = ("P279", "P361")
MAX_CONTENTS_PER_TERM = 100
MAX_PAIRS_PER_RELATION = 20000
SAMPLE_SIZE = 100
random.seed(42)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def targets_from_claims(claims, prop):
    result = set()
    for statement in claims.get(prop, []):
        if not isinstance(statement, dict):
            continue
        if statement.get("rank") == "deprecated":
            continue
        value = (
            statement.get("mainsnak", {})
            .get("datavalue", {})
            .get("value")
        )
        if isinstance(value, dict) and value.get("id"):
            result.add(value["id"])
    return result


def load_relations():
    relations = set()

    for path in sorted(CACHE.glob("entities_p31_*.json")):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        for source, entity in data.get("entities", {}).items():
            if not isinstance(entity, dict) or "missing" in entity:
                continue

            claims = entity.get("claims", {})
            for prop in REL_PROPS:
                for target in targets_from_claims(claims, prop):
                    relations.add((source, prop, target))

    return relations


def main():
    statuses = read_csv(STATUS_PATH)
    candidates = read_csv(CANDIDATE_PATH)
    content_map = read_csv(MAP_PATH)

    if not content_map:
        raise ValueError("키워드-작품 매핑 CSV가 비어 있음")

    print("매핑 CSV 컬럼:", list(content_map[0]))

    required = {"staging_id", "content_id"}
    if not required.issubset(content_map[0]):
        raise ValueError(
            "02_keyword_term_map.csv에서 staging_id/content_id "
            "컬럼을 찾지 못함. 실제 컬럼을 확인해 수정 필요."
        )

    # 1. 키워드 및 QID 매핑
    term_info = {
        row["staging_id"]: row for row in statuses
    }

    qid_to_terms = defaultdict(set)
    term_to_qids = defaultdict(set)
    qid_info = {}

    for row in candidates:
        sid = row["staging_id"]
        qid = row["qid"]
        qid_to_terms[qid].add(sid)
        term_to_qids[sid].add(qid)
        qid_info.setdefault(qid, row)


    for row in candidates:
        sid = row["staging_id"]
        qid = row["qid"]

        # 복수 후보는 간선 분석에서 제외
        if term_info[sid]["status"] != "CANDIDATE_UNIQUE":
            continue

        qid_to_terms[qid].add(sid)
        term_to_qids[sid].add(qid)
        qid_info.setdefault(qid, row)
    
    # 2. 작품 연결
    term_contents = defaultdict(set)
    for row in content_map:
        sid = row["staging_id"]
        cid = row["content_id"]
        if sid and cid:
            term_contents[sid].add(cid)

    qid_contents = defaultdict(set)
    for qid, terms in qid_to_terms.items():
        for sid in terms:
            qid_contents[qid].update(
                term_contents.get(sid, set())
            )

    # 3. 단일 후보 검토 표본
    unique_rows = [
        row for row in statuses
        if row["status"] in (
            "CANDIDATE_UNIQUE", "SINGLE_CANDIDATE"
        )
    ]

    random.shuffle(unique_rows)
    sample = []

    for row in unique_rows[:SAMPLE_SIZE]:
        sid = row["staging_id"]
        qid = next(iter(term_to_qids[sid]), "")
        info = qid_info.get(qid, {})

        sample.append({
            "staging_id": sid,
            "term": row["normalized_term"],
            "qid": qid,
            "label_ko": info.get("label_ko", ""),
            "label_en": info.get("label_en", ""),
            "description_ko": info.get("description_ko", ""),
            "description_en": info.get("description_en", ""),
            "p31_types": info.get("p31_types", ""),
            "review": "",
        })

    save_csv(
        OUT / "05_unique_candidate_review.csv",
        sample,
        [
            "staging_id", "term", "qid",
            "label_ko", "label_en",
            "description_ko", "description_en",
            "p31_types", "review",
        ],
    )

    # 4. 실제 캐시 관계 중 양쪽이 키워드 후보인 경우
    all_relations = load_relations()
    linked_relations = []

    for source, prop, target in sorted(all_relations):
        if source not in qid_to_terms:
            continue
        if target not in qid_to_terms:
            continue

        linked_relations.append((source, prop, target))

    relation_rows = []
    pair_union = set()
    skipped_hub_relations = 0
    skipped_large_relations = 0
    relation_stats = Counter()

    for source, prop, target in linked_relations:
        source_terms = qid_to_terms[source]
        target_terms = qid_to_terms[target]

        a_contents = qid_contents[source]
        b_contents = qid_contents[target]

        a_size = len(a_contents)
        b_size = len(b_contents)
        shared = len(a_contents & b_contents)
        potential_pairs = a_size * b_size - shared

        relation_stats[prop] += 1

        skip_reason = ""

        if (
            a_size > MAX_CONTENTS_PER_TERM
            or b_size > MAX_CONTENTS_PER_TERM
        ):
            skip_reason = "HUB_LIMIT"
            skipped_hub_relations += 1
        elif potential_pairs > MAX_PAIRS_PER_RELATION:
            skip_reason = "PAIR_LIMIT"
            skipped_large_relations += 1
        else:
            for a in a_contents:
                for b in b_contents:
                    if a == b:
                        continue
                    pair_union.add(tuple(sorted((a, b))))

        relation_rows.append({
            "source_qid": source,
            "property": prop,
            "target_qid": target,
            "source_terms": "|".join(sorted(
                term_info[s]["normalized_term"]
                for s in source_terms if s in term_info
            )),
            "target_terms": "|".join(sorted(
                term_info[s]["normalized_term"]
                for s in target_terms if s in term_info
            )),
            "source_content_count": a_size,
            "target_content_count": b_size,
            "potential_cross_content_pairs": potential_pairs,
            "skip_reason": skip_reason,
        })

    save_csv(
        OUT / "06_candidate_relations.csv",
        relation_rows,
        [
            "source_qid", "property", "target_qid",
            "source_terms", "target_terms",
            "source_content_count", "target_content_count",
            "potential_cross_content_pairs", "skip_reason",
        ],
    )

    report = {
        "interpretation": (
            "검증 전 키워드-QID 후보와 Wikidata 관계로 "
            "만든 잠재적 연결. 실제 유효 간선 수가 아님."
        ),
        "unique_candidate_review_sample": len(sample),
        "cached_relation_triples": len(all_relations),
        "relations_with_both_qids_matched": len(linked_relations),
        "relation_property_counts": dict(relation_stats),
        "hub_skipped_relations": skipped_hub_relations,
        "large_skipped_relations": skipped_large_relations,
        "unique_potential_content_pairs_within_limits": len(pair_union),
        "baseline_comparison": "NOT_COMPUTED",
        "validation_status": "UNVERIFIED",
    }

    with (OUT / "07_candidate_relation_report.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== 후보 관계 분석 ===")
    print("전체 캐시 관계:", len(all_relations))
    print("양쪽 QID가 키워드 후보인 관계:", len(linked_relations))
    print("속성별:", dict(relation_stats))
    print("허브 제한 제외:", skipped_hub_relations)
    print("대량 조합 제외:", skipped_large_relations)
    print("제한 내 잠재 작품 쌍:", len(pair_union))
    print("검토 표본:", len(sample))
    print("\n저장 위치:", OUT)


if __name__ == "__main__":
    main()
