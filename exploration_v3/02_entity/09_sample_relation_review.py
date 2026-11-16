import csv
import json
import random
from collections import defaultdict
from pathlib import Path

BASE = Path("../staging/entity")
RNG = random.Random(42)

def read_csv(name):
    with (BASE / name).open(
        encoding="utf-8-sig", newline=""
    ) as f:
        return list(csv.DictReader(f))

def methods(value):
    return sorted(set(
        x.strip() for x in value.split("|") if x.strip()
    ))

candidates = defaultdict(list)
for row in read_csv("03_wikidata_candidates.csv"):
    candidates[(row["staging_id"], row["qid"])].append(row)

groups = defaultdict(list)

for row in read_csv("12_strict_relation_term_pairs.csv"):
    if row["flags"]:
        continue

    for e in json.loads(row["evidence"]):
        key = (
            e["source_qid"],
            e["property"],
            e["target_qid"],
        )

        source = candidates.get(
            (row["source_staging_id"], e["source_qid"]), []
        )
        target = candidates.get(
            (row["target_staging_id"], e["target_qid"]), []
        )

        if not source or not target:
            raise ValueError(f"Missing candidate: {key}")

        groups[key].append({
            "source_term": row["source_term"],
            "target_term": row["target_term"],
            "source_methods": "|".join(sorted(set(
                m for c in source
                for m in methods(c["match_evidence"])
            ))),
            "target_methods": "|".join(sorted(set(
                m for c in target
                for m in methods(c["match_evidence"])
            ))),
            "source_label": source[0]["label_ko"] or source[0]["label_en"],
            "target_label": target[0]["label_ko"] or target[0]["label_en"],
            "source_description": (
                source[0]["description_ko"]
                or source[0]["description_en"]
            ),
            "target_description": (
                target[0]["description_ko"]
                or target[0]["description_en"]
            ),
            "source_risk": source[0]["risk_flag"],
            "target_risk": target[0]["risk_flag"],
        })

# 기존 영향도 분석의 순위를 사용한다.
# 16번 파일의 키워드 간선 순위와
# 12번 파일의 QID 관계 근거를 연결한다.

term_to_qids = defaultdict(set)

for row in read_csv("12_strict_relation_term_pairs.csv"):
    if row["flags"]:
        continue

    term_key = (
        row["source_staging_id"],
        row["target_staging_id"],
    )

    for e in json.loads(row["evidence"]):
        qid_key = (
            e["source_qid"],
            e["property"],
            e["target_qid"],
        )
        term_to_qids[term_key].add(qid_key)

# QID 관계별로 연결된 키워드 간선의
# 최고 영향도 순위를 기록한다.
qid_best_rank = {}

for row in read_csv("16_relation_contribution.csv"):
    term_key = (
        row["source_staging_id"],
        row["target_staging_id"],
    )

    rank = int(row["rank"])

    for qid_key in term_to_qids.get(term_key, set()):
        if qid_key not in qid_best_rank:
            qid_best_rank[qid_key] = rank
        else:
            qid_best_rank[qid_key] = min(
                qid_best_rank[qid_key], rank
            )

keys = sorted(groups)

# 가장 높은 영향도의 키워드 간선을 가진
# QID 관계부터 선정한다.
ranked = sorted(
    keys,
    key=lambda k: (
        qid_best_rank.get(k, float("inf")),
        k,
    )
)

top = ranked[:25]
remaining = [k for k in keys if k not in set(top)]

selected = (
    [("TOP", k) for k in top]
    + [
        ("RANDOM", k)
        for k in RNG.sample(
            remaining,
            min(25, len(remaining))
        )
    ]
)

output = [] 

for sample_type, key in selected:
    examples = groups[key]

    # 대표 사례는 정렬로 고정하여 재현 가능하게 한다.
    example = sorted(
        examples,
        key=lambda x: (
            x["source_term"],
            x["target_term"]
        )
    )[0]

    output.append({
        "sample_type": sample_type,
        "source_qid": key[0],
        "property": key[1],
        "target_qid": key[2],
        "keyword_edge_count": len(examples),
        "source_term": example["source_term"],
        "target_term": example["target_term"],
        **{
            k: v for k, v in example.items()
            if k not in ("source_term", "target_term")
        },
        "source_mapping_review": "",
        "target_mapping_review": "",
        "relation_review": "",
        "notes": "",
    })

out_path = BASE / "22_relation_review_sample.csv"

with out_path.open(
    "w", encoding="utf-8-sig", newline=""
) as f:
    writer = csv.DictWriter(f, fieldnames=output[0].keys())
    writer.writeheader()
    writer.writerows(output)

print("고유 QID 관계:", len(groups))
print("검토 표본:", len(output))
print("저장:", out_path)
print("표본 유형:", {
    x: sum(r["sample_type"] == x for r in output)
    for x in sorted(set(r["sample_type"] for r in output))
})
