
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "exploration_v3/staging"
OUT = STAGING / "entity"

EDGES_PATH = OUT / "12_strict_relation_term_pairs.csv"
MAP_PATH = STAGING / "02_keyword_term_map.csv"

SEED = 42
TOP_N = 30
SAMPLES_PER_EDGE = 5

random.seed(SEED)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def make_pair(a, b):
    if a == b:
        return None
    return tuple(sorted((a, b)))


def main():
    edges = read_csv(EDGES_PATH)
    mappings = read_csv(MAP_PATH)

    term_contents = defaultdict(set)
    content_terms = defaultdict(set)

    for row in mappings:
        sid = row["staging_id"]
        cid = row["content_id"]

        if not sid or not cid:
            continue

        term_contents[sid].add(cid)
        content_terms[cid].add(sid)

    # 간선별 신규 작품 쌍
    edge_data = []
    global_new_pairs = set()
    property_pairs = defaultdict(set)
    property_edges = Counter()

    for row in edges:
        if row["flags"]:
            continue

        source_sid = row["source_staging_id"]
        target_sid = row["target_staging_id"]

        source_contents = term_contents[source_sid]
        target_contents = term_contents[target_sid]

        evidence = json.loads(row["evidence"])
        properties = sorted({
            item["property"]
            for item in evidence
        })

        new_pairs = set()
        existing_pairs = set()

        for a in source_contents:
            for b in target_contents:
                pair = make_pair(a, b)

                if pair is None:
                    continue

                # A0: 동일 키워드를 공유하는 작품 쌍
                if content_terms[a] & content_terms[b]:
                    existing_pairs.add(pair)
                else:
                    new_pairs.add(pair)

        global_new_pairs.update(new_pairs)

        for prop in properties:
            property_pairs[prop].update(new_pairs)
            property_edges[prop] += 1

        edge_data.append({
            "source_sid": source_sid,
            "target_sid": target_sid,
            "source_term": row["source_term"],
            "target_term": row["target_term"],
            "properties": properties,
            "evidence_count": len(evidence),
            "new_pairs": new_pairs,
            "existing_pairs": existing_pairs,
            "potential_pairs": len(new_pairs | existing_pairs),
        })

    # 신규 연결 수 기준 정렬
    edge_data.sort(
        key=lambda x: (
            -len(x["new_pairs"]),
            x["source_term"],
            x["target_term"],
        )
    )

    total_new = len(global_new_pairs)

    # 상위 간선 누적 기여도: 중복 제거
    cumulative = set()
    contribution_rows = []
    top_samples = []

    for rank, edge in enumerate(edge_data, start=1):
        incremental = edge["new_pairs"] - cumulative
        cumulative.update(edge["new_pairs"])

        contribution_rows.append({
            "rank": rank,
            "source_term": edge["source_term"],
            "target_term": edge["target_term"],
            "source_staging_id": edge["source_sid"],
            "target_staging_id": edge["target_sid"],
            "properties": "|".join(edge["properties"]),
            "evidence_count": edge["evidence_count"],
            "potential_pairs": edge["potential_pairs"],
            "a0_existing_pairs": len(edge["existing_pairs"]),
            "a0_new_pairs": len(edge["new_pairs"]),
            "incremental_new_pairs": len(incremental),
            "cumulative_unique_new_pairs": len(cumulative),
            "cumulative_coverage_pct": round(
                100 * len(cumulative) / total_new, 2
            ) if total_new else 0,
        })

        if rank <= TOP_N:
            sample = random.sample(
                sorted(edge["new_pairs"]),
                min(SAMPLES_PER_EDGE, len(edge["new_pairs"]))
            )

            for a, b in sample:
                top_samples.append({
                    "rank": rank,
                    "source_term": edge["source_term"],
                    "target_term": edge["target_term"],
                    "properties": "|".join(edge["properties"]),
                    "content_id_a": a,
                    "content_id_b": b,
                    "shared_a0_keyword": False,
                    "review_validity": "",
                    "review_usefulness": "",
                    "notes": "",
                })

    # 상위 N개 간선의 합집합 기준 기여도
    top_coverage = {}

    for n in (5, 10, 30):
        union = set()

        for edge in edge_data[:n]:
            union.update(edge["new_pairs"])

        top_coverage[str(n)] = {
            "edges_considered": min(n, len(edge_data)),
            "unique_new_pairs": len(union),
            "share_of_total_pct": round(
                100 * len(union) / total_new, 2
            ) if total_new else 0,
        }

    # P279/P361 합집합 기준 비교
    property_stats = {}

    for prop, pairs in sorted(property_pairs.items()):
        property_stats[prop] = {
            "eligible_term_edges": property_edges[prop],
            "unique_new_pairs": len(pairs),
            "share_of_total_pct": round(
                100 * len(pairs) / total_new, 2
            ) if total_new else 0,
        }

    # 속성 간 겹침
    property_overlap = {}

    props = sorted(property_pairs)

    for i, a in enumerate(props):
        for b in props[i + 1:]:
            property_overlap[f"{a}&{b}"] = len(
                property_pairs[a] & property_pairs[b]
            )

    write_csv(
        OUT / "16_relation_contribution.csv",
        contribution_rows,
        [
            "rank",
            "source_term",
            "target_term",
            "source_staging_id",
            "target_staging_id",
            "properties",
            "evidence_count",
            "potential_pairs",
            "a0_existing_pairs",
            "a0_new_pairs",
            "incremental_new_pairs",
            "cumulative_unique_new_pairs",
            "cumulative_coverage_pct",
        ],
    )

    write_csv(
        OUT / "17_top_relation_samples.csv",
        top_samples,
        [
            "rank",
            "source_term",
            "target_term",
            "properties",
            "content_id_a",
            "content_id_b",
            "shared_a0_keyword",
            "review_validity",
            "review_usefulness",
            "notes",
        ],
    )

    report = {
        "interpretation": (
            "단일 후보 기반 관계의 A0 대비 신규 작품 쌍 기여도. "
            "간선별 연결은 미검증이며 SAME 기준선은 미적용."
        ),
        "eligible_term_edges": len(edge_data),
        "total_unique_new_pairs": total_new,
        "top_coverage": top_coverage,
        "property_stats": property_stats,
        "property_overlap": property_overlap,
        "top_30": contribution_rows[:30],
        "sample_rows": len(top_samples),
        "a0_definition": "same staging_id keyword shared",
        "same_baseline": "NOT_COMPUTED",
        "validation_status": "UNVERIFIED",
        "database_writes": False,
        "new_api_requests": 0,
    }

    with (
        OUT / "18_relation_contribution_report.json"
    ).open("w", encoding="utf-8") as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("\n=== 관계별 신규 연결 기여도 ===")
    print("분석 간선:", len(edge_data))
    print("전체 신규 작품 쌍:", total_new)

    print("\n[상위 간선 커버리지]")
    for n, info in top_coverage.items():
        print(
            f"상위 {n}개:",
            info["unique_new_pairs"],
            f"({info['share_of_total_pct']}%)",
        )

    print("\n[속성별]")
    for prop, info in property_stats.items():
        print(
            prop,
            "간선:", info["eligible_term_edges"],
            "신규 쌍:", info["unique_new_pairs"],
        )

    print("\n[상위 15개 간선]")
    for row in contribution_rows[:15]:
        print(
            f"{row['rank']:>2}. "
            f"{row['source_term']} → {row['target_term']} "
            f"| 신규 {row['a0_new_pairs']:,} "
            f"| 순증 기여 {row['incremental_new_pairs']:,}"
        )

    print("\n저장 위치:", OUT)


if __name__ == "__main__":
    main()
