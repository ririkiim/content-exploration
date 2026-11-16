
from pathlib import Path
from collections import defaultdict, Counter
import json
import pandas as pd

BASE = Path(__file__).resolve().parent
STAGING = BASE / "staging"

TERMS_FILE = STAGING / "01_keyword_terms.csv"
MAP_FILE = STAGING / "02_keyword_term_map.csv"

PAIRS = [
    ("일제시대", "일제강점기"),
    ("이조시대", "조선시대"),
]

def load_data():
    terms = pd.read_csv(TERMS_FILE, dtype={"staging_id": str})
    mapping = pd.read_csv(
        MAP_FILE,
        dtype={"staging_id": str, "content_id": str},
    )

    required_terms = {"staging_id", "normalized_term"}
    required_map = {"staging_id", "content_id"}

    assert required_terms <= set(terms.columns)
    assert required_map <= set(mapping.columns)
    assert terms["staging_id"].is_unique

    term_to_id = dict(zip(
        terms["normalized_term"], terms["staging_id"]
    ))

    # A0: 콘텐츠가 공유하는 정규화 키워드
    term_contents = defaultdict(set)
    content_terms = defaultdict(set)

    for content_id, term_id in (
        mapping[["content_id", "staging_id"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    ):
        term_contents[term_id].add(content_id)
        content_terms[content_id].add(term_id)

    return term_to_id, term_contents, content_terms, mapping


def analyze_pair(a, b, term_to_id, term_contents, content_terms):
    if a not in term_to_id or b not in term_to_id:
        raise ValueError(f"용어 없음: {a}, {b}")

    a_id, b_id = term_to_id[a], term_to_id[b]
    A = term_contents[a_id]
    B = term_contents[b_id]

    a_only = A - B
    b_only = B - A
    both = A & B

    # SAME으로 새로 연결 가능한 작품 쌍.
    # A0에서 이미 연결된 쌍은 제외해야 함.
    #
    # 단순한 전체 콘텐츠 조합 대신,
    # 각 작품의 키워드 집합을 비교한다.
    # 현재 두 쌍의 규모에서는 이 방식으로 충분함.

    new_pairs = []
    already_a0 = 0

    neighbor_increase = Counter()

    for x in sorted(a_only):
        x_terms = content_terms[x]

        for y in sorted(b_only):
            if x_terms.isdisjoint(content_terms[y]):
                new_pairs.append((x, y))
                neighbor_increase[x] += 1
                neighbor_increase[y] += 1
            else:
                already_a0 += 1

    possible = len(a_only) * len(b_only)

    # 시드별 증가량: 증가하지 않은 작품도 0으로 포함
    all_seeds = A | B
    increases = [neighbor_increase[c] for c in all_seeds]

    result = {
        "pair": [a, b],
        "a_total": len(A),
        "b_total": len(B),
        "a_only": len(a_only),
        "b_only": len(b_only),
        "both": len(both),
        "union": len(A | B),
        "same_cross_pairs": possible,
        "already_connected_in_a0": already_a0,
        "net_new_pairs_vs_a0": len(new_pairs),
        "seed_neighbor_increase": {
            "min": min(increases) if increases else 0,
            "mean": round(sum(increases) / len(increases), 2)
                if increases else 0,
            "max": max(increases) if increases else 0,
            "seeds_with_increase": sum(v > 0 for v in increases),
            "seeds_total": len(all_seeds),
        },
    }

    return result, new_pairs, neighbor_increase


def main():
    term_to_id, term_contents, content_terms, mapping = load_data()

    reports = []
    all_new_pairs = set()
    neighbor_rows = []

    for a, b in PAIRS:
        result, pairs, increases = analyze_pair(
            a, b, term_to_id, term_contents, content_terms
        )
        reports.append(result)

        for x, y in pairs:
            # 순서에 무관한 고유 작품 쌍
            all_new_pairs.add(tuple(sorted((x, y))))

        a_id, b_id = term_to_id[a], term_to_id[b]
        seeds = term_contents[a_id] | term_contents[b_id]

        for content_id in sorted(seeds):
            neighbor_rows.append({
                "pair_a": a,
                "pair_b": b,
                "content_id": content_id,
                "new_neighbors": increases[content_id],
            })

        print(f"\n[{a} ↔ {b}]")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # 두 SAME 간선의 신규 연결이 서로 중복될 수 있음
    summary = {
        "pair_reports": reports,
        "unique_net_new_pairs_across_both": len(all_new_pairs),
        "note": (
            "A0 = 정규화 키워드의 정확 일치. "
            "출처 간 신규 연결은 실제 콘텐츠 출처 매핑이 "
            "확인되지 않아 계산하지 않음."
        ),
    }

    (STAGING / "10_named_period_same_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pd.DataFrame(
        sorted(all_new_pairs),
        columns=["content_id_1", "content_id_2"],
    ).to_csv(
        STAGING / "11_named_period_new_pairs.csv",
        index=False,
        encoding="utf-8-sig",
    )

    pd.DataFrame(neighbor_rows).to_csv(
        STAGING / "12_named_period_seed_increase.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("\n" + "=" * 55)
    print("두 SAME 관계 합산 (중복 제거)")
    print("A0 대비 신규 고유 작품 쌍:", len(all_new_pairs))
    print("결과 저장 완료: staging/10~12")


if __name__ == "__main__":
    main()
