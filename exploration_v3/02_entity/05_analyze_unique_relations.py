
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "exploration_v3/staging"
OUT = STAGING / "entity"

STATUS = OUT / "02_keyword_match_status.csv"
CANDIDATES = OUT / "03_wikidata_candidates.csv"
RELATIONS = OUT / "06_candidate_relations.csv"
MAPPINGS = STAGING / "02_keyword_term_map.csv"

MAX_CONTENTS_PER_TERM = 100
MAX_PAIRS_PER_EDGE = 20000

# 정책 초안: 자동 삭제가 아닌 분석용 제외 표시
META_TERMS = {
    "영화", "작품", "드라마", "도서", "책",
    "film", "movie", "work", "book",
    "human", "person", "organization",
    "사람", "인간", "조직",
}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    statuses = read_csv(STATUS)
    candidates = read_csv(CANDIDATES)
    relations = read_csv(RELATIONS)
    mappings = read_csv(MAPPINGS)

    term_info = {
        row["staging_id"]: row
        for row in statuses
    }

    # QID별 단일 후보 키워드
    unique_qid_terms = defaultdict(set)
    all_qid_terms = defaultdict(set)

    for row in candidates:
        sid = row["staging_id"]
        qid = row["qid"]

        all_qid_terms[qid].add(sid)

        if term_info[sid]["status"] == "CANDIDATE_UNIQUE":
            unique_qid_terms[qid].add(sid)

    # 키워드-작품 매핑
    term_contents = defaultdict(set)
    content_terms = defaultdict(set)

    for row in mappings:
        sid = row["staging_id"]
        cid = row["content_id"]

        if not sid or not cid:
            continue

        term_contents[sid].add(cid)
        content_terms[cid].add(sid)

    # QID 관계의 중복 제거
    qid_relations = {
        (
            row["source_qid"],
            row["property"],
            row["target_qid"],
        )
        for row in relations
    }

    strict_relations = set()
    multi_only_relations = set()

    # 키워드 쌍별 원본 QID 관계 근거
    term_edges = defaultdict(set)
    self_edges = set()

    for source, prop, target in qid_relations:
        source_terms = unique_qid_terms.get(source, set())
        target_terms = unique_qid_terms.get(target, set())

        if not source_terms or not target_terms:
            multi_only_relations.add((source, prop, target))
            continue

        strict_relations.add((source, prop, target))

        for a in source_terms:
            for b in target_terms:
                evidence = (source, prop, target)

                a_text = term_info[a]["normalized_term"]
                b_text = term_info[b]["normalized_term"]

                if a == b or a_text == b_text:
                    self_edges.add((a, b, evidence))
                    continue

                # 방향은 유지
                term_edges[(a, b)].add(evidence)

    # 키워드 쌍별 평가
    rows = []
    raw_pairs = set()
    a0_existing_pairs = set()
    new_pairs = set()

    flagged_counts = Counter()
    eligible_edge_count = 0
    eligible_terms = set()
    eligible_contents = set()

    for (a, b), evidence_set in sorted(term_edges.items()):
        a_text = term_info[a]["normalized_term"]
        b_text = term_info[b]["normalized_term"]

        a_contents = term_contents.get(a, set())
        b_contents = term_contents.get(b, set())

        possible = (
            len(a_contents) * len(b_contents)
            - len(a_contents & b_contents)
        )

        flags = []

        if a_text in META_TERMS or b_text in META_TERMS:
            flags.append("META")

        if (
            len(a_contents) > MAX_CONTENTS_PER_TERM
            or len(b_contents) > MAX_CONTENTS_PER_TERM
        ):
            flags.append("HUB")

        if possible > MAX_PAIRS_PER_EDGE:
            flags.append("PAIR_LIMIT")

        for flag in flags:
            flagged_counts[flag] += 1

        if not flags:
            eligible_edge_count += 1
            eligible_terms.update((a, b))
            eligible_contents.update(a_contents)
            eligible_contents.update(b_contents)

            for cid_a in a_contents:
                for cid_b in b_contents:
                    if cid_a == cid_b:
                        continue

                    pair = tuple(sorted((cid_a, cid_b)))
                    raw_pairs.add(pair)

                    # A0: 두 작품이 공유하는 키워드 존재
                    if content_terms[cid_a] & content_terms[cid_b]:
                        a0_existing_pairs.add(pair)
                    else:
                        new_pairs.add(pair)

        rows.append({
            "source_staging_id": a,
            "target_staging_id": b,
            "source_term": a_text,
            "target_term": b_text,
            "source_content_count": len(a_contents),
            "target_content_count": len(b_contents),
            "potential_pairs": possible,
            "evidence_count": len(evidence_set),
            "evidence": json.dumps(
                [
                    {
                        "source_qid": s,
                        "property": p,
                        "target_qid": t,
                    }
                    for s, p, t in sorted(evidence_set)
                ],
                ensure_ascii=False,
            ),
            "flags": "|".join(flags),
        })

    write_csv(
        OUT / "12_strict_relation_term_pairs.csv",
        rows,
        [
            "source_staging_id",
            "target_staging_id",
            "source_term",
            "target_term",
            "source_content_count",
            "target_content_count",
            "potential_pairs",
            "evidence_count",
            "evidence",
            "flags",
        ],
    )

    all_contents = set(content_terms)

    report = {
        "interpretation": (
            "단일 QID 후보를 이용한 키워드 쌍 관계 분석. "
            "관계의 의미와 유용성은 검증 전 상태."
        ),
        "input_qid_relations": len(qid_relations),
        "strict_qid_relations": len(strict_relations),
        "multi_only_qid_relations": len(multi_only_relations),
        "strict_by_property": dict(Counter(
            prop for _, prop, _ in strict_relations
        )),
        "self_term_evidence_rows": len(self_edges),
        "distinct_directed_term_edges": len(term_edges),
        "eligible_term_edges": eligible_edge_count,
        "flagged_term_edges": dict(flagged_counts),
        "eligible_distinct_terms": len(eligible_terms),
        "eligible_content_count": len(eligible_contents),
        "eligible_content_coverage_percent": round(
            100 * len(eligible_contents) / len(all_contents), 2
        ) if all_contents else 0,
        "raw_potential_content_pairs": len(raw_pairs),
        "a0_existing_content_pairs": len(a0_existing_pairs),
        "a0_new_potential_content_pairs": len(new_pairs),
        "same_baseline": "NOT_COMPUTED",
        "validation_status": "UNVERIFIED",
        "database_writes": False,
    }

    with (
        OUT / "13_strict_relation_report.json"
    ).open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== 단일 후보 키워드 관계 분석 ===")
    print("입력 QID 관계:", len(qid_relations))
    print("STRICT QID 관계:", len(strict_relations))
    print("복수 후보 의존 관계:", len(multi_only_relations))
    print("키워드 간선:", len(term_edges))
    print("허브/메타 등 제외 후:", eligible_edge_count)
    print("분석 대상 작품:", len(eligible_contents))
    print("잠재 작품 쌍:", len(raw_pairs))
    print("A0 기존 연결:", len(a0_existing_pairs))
    print("A0 대비 신규 후보:", len(new_pairs))
    print("\n저장 위치:", OUT)


if __name__ == "__main__":
    main()
