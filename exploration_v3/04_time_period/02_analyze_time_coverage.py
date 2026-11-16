
import json
from pathlib import Path

import pandas as pd


# ============================================================
# 0. 경로
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STAGING = BASE_DIR / "staging"

TERMS_FILE = STAGING / "01_keyword_terms.csv"
MAP_FILE = STAGING / "02_keyword_term_map.csv"
PERIOD_FILE = STAGING / "03_time_periods.csv"
REVIEW_FILE = STAGING / "04_time_parse_review.csv"

TOTAL_CONTENTS = 43601


# ============================================================
# 1. 유틸리티
# ============================================================

def save_csv(df, filename):
    df.to_csv(
        STAGING / filename,
        index=False,
        encoding="utf-8-sig",
    )


def pair_count(n):
    n = int(n)
    return n * (n - 1) // 2


def pct(n, denominator):
    if denominator == 0:
        return 0.0
    return round(n / denominator * 100, 2)


def to_int_dict(series):
    return {
        str(k): int(v)
        for k, v in series.items()
    }


# ============================================================
# 2. 데이터 로드
# ============================================================

def main():

    terms = pd.read_csv(TERMS_FILE)
    mapping = pd.read_csv(MAP_FILE)
    periods = pd.read_csv(PERIOD_FILE)
    reviews = pd.read_csv(REVIEW_FILE)

    # 필수 컬럼 확인
    required = {
        "terms": (
            terms,
            {"staging_id", "normalized_term"},
        ),
        "mapping": (
            mapping,
            {
                "keyword_id",
                "content_id",
                "source_name",
                "staging_id",
            },
        ),
        "periods": (
            periods,
            {
                "staging_id",
                "normalized_term",
                "period_type",
                "status",
            },
        ),
        "reviews": (
            reviews,
            {
                "staging_id",
                "normalized_term",
                "review_reason",
            },
        ),
    }

    for name, (df, columns) in required.items():
        missing = columns - set(df.columns)
        if missing:
            raise ValueError(
                f"{name} 필수 컬럼 누락: {missing}"
            )

    assert terms["staging_id"].is_unique
    assert mapping["keyword_id"].is_unique
    assert periods["staging_id"].is_unique
    assert reviews["staging_id"].is_unique

    assert set(mapping["staging_id"]).issubset(
        set(terms["staging_id"])
    )

    assert set(periods["staging_id"]).isdisjoint(
        set(reviews["staging_id"])
    )

    # ========================================================
    # 3. 기본 커버리지
    # ========================================================

    keyword_content_ids = set(mapping["content_id"])

    period_map = mapping.merge(
        periods[
            [
                "staging_id",
                "normalized_term",
                "period_type",
                "status",
            ]
        ],
        on="staging_id",
        how="inner",
        validate="many_to_one",
    )

    review_map = mapping.merge(
        reviews[
            [
                "staging_id",
                "normalized_term",
                "review_reason",
            ]
        ],
        on="staging_id",
        how="inner",
        validate="many_to_one",
    )

    parsed_content_ids = set(period_map["content_id"])

    # 잠정 명명 시대를 제외한 커버리지
    confirmed_map = period_map[
        period_map["status"] == "parsed"
    ].copy()

    confirmed_content_ids = set(
        confirmed_map["content_id"]
    )

    provisional_map = period_map[
        period_map["status"] == "provisional"
    ].copy()

    provisional_content_ids = set(
        provisional_map["content_id"]
    )

    review_content_ids = set(review_map["content_id"])

    # ========================================================
    # 4. 용어별 df / 잠재 쌍 수
    # ========================================================

    term_frequency = (
        period_map.groupby(
            [
                "staging_id",
                "normalized_term",
                "period_type",
                "status",
            ],
            dropna=False,
        )
        .agg(
            content_count=("content_id", "nunique"),
            keyword_rows=("keyword_id", "count"),
            source_count=("source_name", "nunique"),
        )
        .reset_index()
    )

    term_frequency["potential_pairs"] = (
        term_frequency["content_count"]
        .apply(pair_count)
    )

    term_frequency = term_frequency.sort_values(
        ["potential_pairs", "content_count"],
        ascending=False,
    )

    save_csv(
        term_frequency,
        "06_time_term_frequency.csv",
    )

    # ========================================================
    # 5. 검토 후보별 빈도
    # ========================================================

    review_frequency = (
        review_map.groupby(
            [
                "staging_id",
                "normalized_term",
                "review_reason",
            ],
            dropna=False,
        )
        .agg(
            content_count=("content_id", "nunique"),
            keyword_rows=("keyword_id", "count"),
        )
        .reset_index()
    )

    review_frequency["potential_pairs"] = (
        review_frequency["content_count"]
        .apply(pair_count)
    )

    review_frequency = review_frequency.sort_values(
        ["content_count", "normalized_term"],
        ascending=[False, True],
    )

    save_csv(
        review_frequency,
        "07_time_review_frequency.csv",
    )

    # ========================================================
    # 6. 콘텐츠별 시대 표현 분포
    # ========================================================

    content_distribution = (
        period_map.groupby("content_id")
        .agg(
            time_term_count=("staging_id", "nunique"),
            time_keyword_rows=("keyword_id", "count"),
            time_source_count=("source_name", "nunique"),
        )
        .reset_index()
    )

    confirmed_counts = (
        confirmed_map.groupby("content_id")["staging_id"]
        .nunique()
        .rename("confirmed_time_terms")
    )

    provisional_counts = (
        provisional_map.groupby("content_id")["staging_id"]
        .nunique()
        .rename("provisional_time_terms")
    )

    content_distribution = (
        content_distribution
        .merge(
            confirmed_counts,
            on="content_id",
            how="left",
        )
        .merge(
            provisional_counts,
            on="content_id",
            how="left",
        )
    )

    count_columns = [
        "confirmed_time_terms",
        "provisional_time_terms",
    ]

    content_distribution[count_columns] = (
        content_distribution[count_columns]
        .fillna(0)
        .astype(int)
    )

    save_csv(
        content_distribution,
        "08_time_content_distribution.csv",
    )

    # ========================================================
    # 7. 출처별 시대 표현 빈도
    # ========================================================

    source_frequency = (
        period_map.groupby(
            [
                "source_name",
                "normalized_term",
                "status",
            ],
            dropna=False,
        )
        .agg(
            content_count=("content_id", "nunique"),
            keyword_rows=("keyword_id", "count"),
        )
        .reset_index()
        .sort_values(
            ["source_name", "content_count"],
            ascending=[True, False],
        )
    )

    save_csv(
        source_frequency,
        "09_time_source_frequency.csv",
    )

    # ========================================================
    # 8. 보고서
    # ========================================================

    duplicate_term_contents = int(
        (
            content_distribution["time_term_count"] >= 2
        ).sum()
    )

    total_potential_pairs = int(
        term_frequency["potential_pairs"].sum()
    )

    top_terms = []

    for row in term_frequency.head(15).itertuples():
        top_terms.append({
            "term": row.normalized_term,
            "content_count": int(row.content_count),
            "potential_pairs": int(row.potential_pairs),
            "status": row.status,
        })

    # 보류 용어 중 특히 확인할 표현
    watch_terms = [
        "2000년대",
        "2000s",
        "1900년대",
        "1900s",
        "1200년대",
        "1400년대",
        "1700년대",
        "1800년대",
    ]

    watch_frequency = {}

    for term in watch_terms:
        matched = review_frequency[
            review_frequency["normalized_term"] == term
        ]

        watch_frequency[term] = (
            int(matched.iloc[0]["content_count"])
            if not matched.empty
            else 0
        )

    report = {
        "input": {
            "total_contents_reference": TOTAL_CONTENTS,
            "keyword_rows": len(mapping),
            "keyword_terms": len(terms),
            "parsed_time_terms": len(periods),
            "review_time_terms": len(reviews),
        },
        "coverage": {
            "contents_with_any_keyword": len(
                keyword_content_ids
            ),
            "contents_with_parsed_time": len(
                parsed_content_ids
            ),
            "contents_with_confirmed_time": len(
                confirmed_content_ids
            ),
            "contents_with_provisional_time": len(
                provisional_content_ids
            ),
            "contents_with_review_time": len(
                review_content_ids
            ),
            "parsed_coverage_all_pct": pct(
                len(parsed_content_ids),
                TOTAL_CONTENTS,
            ),
            "parsed_coverage_keyworded_pct": pct(
                len(parsed_content_ids),
                len(keyword_content_ids),
            ),
            "confirmed_coverage_all_pct": pct(
                len(confirmed_content_ids),
                TOTAL_CONTENTS,
            ),
            "confirmed_coverage_keyworded_pct": pct(
                len(confirmed_content_ids),
                len(keyword_content_ids),
            ),
            "review_only_contents": len(
                review_content_ids - parsed_content_ids
            ),
            "provisional_only_contents": len(
                provisional_content_ids
                - confirmed_content_ids
            ),
        },
        "hub_risk": {
            "sum_term_potential_pairs": (
                total_potential_pairs
            ),
            "note": (
                "Term-level pair counts are summed. "
                "The same content pair may appear "
                "under multiple terms."
            ),
            "top_15_terms": top_terms,
        },
        "content_distribution": {
            "contents_with_2plus_time_terms": (
                duplicate_term_contents
            ),
            "time_term_count_distribution": to_int_dict(
                content_distribution["time_term_count"]
                .value_counts()
                .sort_index()
            ),
        },
        "ambiguous_term_frequency": watch_frequency,
        "source_distribution": to_int_dict(
            period_map.groupby("source_name")["content_id"]
            .nunique()
        ),
        "sanity_checks": {
            "21st_century_in_terms": bool(
                (
                    terms["normalized_term"]
                    == "21st century"
                ).any()
            ),
            "21st_century_in_parsed": bool(
                (
                    periods["normalized_term"]
                    == "21st century"
                ).any()
            ),
        },
    }

    with open(
        STAGING / "05_time_coverage_report.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # ========================================================
    # 9. 출력
    # ========================================================

    print("\n===== TIME COVERAGE REPORT =====")

    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    ))

    print("\n생성 완료:")
    print(" - 05_time_coverage_report.json")
    print(" - 06_time_term_frequency.csv")
    print(" - 07_time_review_frequency.csv")
    print(" - 08_time_content_distribution.csv")
    print(" - 09_time_source_frequency.csv")


if __name__ == "__main__":
    main()
