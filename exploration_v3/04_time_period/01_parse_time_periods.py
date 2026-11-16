
import json
import re
from pathlib import Path

import pandas as pd


# ============================================================
# 0. 경로 설정
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INPUT = BASE_DIR / "staging" / "01_keyword_terms.csv"
OUTPUT = BASE_DIR / "staging"

OUTPUT.mkdir(exist_ok=True)


# ============================================================
# 1. 명명 시대 사전
# 범위는 초기 정책값이며 검증 후 확정
# ============================================================

NAMED_PERIODS = {
    "일제강점기": (1910, 1945),
    "일제시대": (1910, 1945),
    "조선시대": (1392, 1897),
    "고려시대": (918, 1392),
    "대한제국": (1897, 1910),
}

# 정확한 범위가 불분명한 표현은 자동 처리하지 않음
REVIEW_TERMS = {
    "삼국시대",
    "조선초기",
    "조선중기",
    "조선후기",
    "조선말기",
    "고려말",
    "고려말기",
    "냉전",
    "냉전시대",
}


# ============================================================
# 2. 전체 일치 정규식
# ============================================================

YEAR_KO = re.compile(r"^(\d{4})년$")
DECADE_KO = re.compile(r"^(\d{4})년대$")
DECADE_EN = re.compile(r"^(\d{4})s$")
CENTURY_KO = re.compile(r"^(\d{1,2})세기$")
CENTURY_EN = re.compile(
    r"^(\d{1,2})(?:st|nd|rd|th) century$"
)

PARTIAL_DECADE = re.compile(
    r"^(\d{4})년대(초|중|후반|초반|중반|말)$"
)

PARTIAL_CENTURY = re.compile(
    r"^(\d{1,2})세기(초|중|말|초반|중반|후반)$"
)

NAMED_PERIOD_HINT = re.compile(
    r"^(?:"
    r"삼국시대|신라시대|석기시대|원시시대|"
    r"이조시대|조선왕조|조선시대말엽|"
    r"조선초기|조선중기|조선후기|조선말기|"
    r"고려말|고려말기|냉전|냉전시대"
    r")$"
)

COMPOSITE_TIME_HINT = re.compile(
    r"(?:\d{4}년(?:대)?|\d{4}s|"
    r"\d{1,2}세기|"
    r"\d{1,2}(?:st|nd|rd|th) century)"
)

# ============================================================
# 3. 파싱 함수
# ============================================================

def parse_time(term: str):

    # 3-1. 명명 시대
    if term in NAMED_PERIODS:
        start, end = NAMED_PERIODS[term]

        return {
            "period_type": "named_period",
            "start_year": start,
            "end_year": end,
            "precision": "named",
            "parser_rule": "named_whitelist",
            "status": "provisional",
        }

    # 3-2. 검토 대상
    if term in REVIEW_TERMS:
        return None

    # 3-3. 특정 연도
    match = YEAR_KO.fullmatch(term)

    if match:
        year = int(match.group(1))

        return {
            "period_type": "year",
            "start_year": year,
            "end_year": year,
            "precision": "exact",
            "parser_rule": "year_ko",
            "status": "parsed",
        }

    # 3-4. 연대 (한국어/영어)
    for pattern, rule in [
        (DECADE_KO, "decade_ko"),
        (DECADE_EN, "decade_en"),
    ]:
        match = pattern.fullmatch(term)

        if match:
            year = int(match.group(1))

            # 1980년대 / 1980s 등
            if year % 10 != 0:
                return None

            # 1900년대 / 1900s처럼 해석이 모호한 표현은 검토
            if year % 100 == 0:
                return None

            return {
                "period_type": "decade",
                "start_year": year,
                "end_year": year + 9,
                "precision": "decade",
                "parser_rule": rule,
                "status": "parsed",
            }

    # 3-5. 세기 (한국어/영어)
    for pattern, rule in [
        (CENTURY_KO, "century_ko"),
        (CENTURY_EN, "century_en"),
    ]:
        match = pattern.fullmatch(term)

        if match:
            century = int(match.group(1))

            if century < 1:
                return None

            start = (century - 1) * 100 + 1
            end = century * 100

            return {
                "period_type": "century",
                "start_year": start,
                "end_year": end,
                "precision": "century",
                "parser_rule": rule,
                "status": "parsed",
            }

    return None


# ============================================================
# 4. 검토 사유 분류
# ============================================================

def review_reason(term: str):

    if term in REVIEW_TERMS:
        return "named_period_needs_review"

    if PARTIAL_DECADE.fullmatch(term):
        return "partial_decade"

    if PARTIAL_CENTURY.fullmatch(term):
        return "partial_century"

    # 1900년대 / 1900s 등
    for pattern in [DECADE_KO, DECADE_EN]:
        match = pattern.fullmatch(term)

        if match and int(match.group(1)) % 100 == 0:
            return "ambiguous_century_expression"

    if NAMED_PERIOD_HINT.fullmatch(term):
        return "named_period_needs_review"

    if COMPOSITE_TIME_HINT.search(term):
        return "composite_time_expression"

    return None

# ============================================================
# 5. 실행
# ============================================================

def main():

    terms = pd.read_csv(
        INPUT,
        dtype={"normalized_term": "string"}
    )

    required = {"staging_id", "normalized_term"}

    if not required.issubset(terms.columns):
        raise ValueError(
            f"필수 컬럼 누락: {required - set(terms.columns)}"
        )

    periods = []
    reviews = []

    for row in terms.itertuples(index=False):

        term = str(row.normalized_term)
        staging_id = int(row.staging_id)

        parsed = parse_time(term)

        if parsed is not None:
            periods.append({
                "staging_id": staging_id,
                "normalized_term": term,
                **parsed,
            })
            continue

        reason = review_reason(term)

        if reason is not None:
            reviews.append({
                "staging_id": staging_id,
                "normalized_term": term,
                "review_reason": reason,
                "status": "pending",
            })

    period_columns = [
        "staging_id",
        "normalized_term",
        "period_type",
        "start_year",
        "end_year",
        "precision",
        "parser_rule",
        "status",
    ]

    review_columns = [
        "staging_id",
        "normalized_term",
        "review_reason",
        "status",
    ]

    period_df = pd.DataFrame(
        periods, columns=period_columns
    )

    review_df = pd.DataFrame(
        reviews, columns=review_columns
    )

    # ========================================================
    # 6. 검증
    # ========================================================

    assert terms["staging_id"].is_unique
    assert terms["normalized_term"].is_unique
    assert period_df["staging_id"].is_unique
    assert review_df["staging_id"].is_unique

    assert set(period_df["staging_id"]).isdisjoint(
        set(review_df["staging_id"])
    )

    if not period_df.empty:
        assert (
            period_df["start_year"]
            <= period_df["end_year"]
        ).all()

    # ========================================================
    # 7. 저장
    # ========================================================

    period_df.to_csv(
        OUTPUT / "03_time_periods.csv",
        index=False,
        encoding="utf-8-sig",
    )

    review_df.to_csv(
        OUTPUT / "04_time_parse_review.csv",
        index=False,
        encoding="utf-8-sig",
    )

    report = {
        "input_terms": len(terms),
        "parsed_periods": len(period_df),
        "review_candidates": len(review_df),
        "unclassified_terms": (
            len(terms) - len(period_df) - len(review_df)
        ),
        "period_types": (
            period_df["period_type"]
            .value_counts()
            .to_dict()
        ),
        "parser_rules": (
            period_df["parser_rule"]
            .value_counts()
            .to_dict()
        ),
        "review_reasons": (
            review_df["review_reason"]
            .value_counts()
            .to_dict()
        ),
    }

    with open(
        OUTPUT / "02_time_parse_report.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("\n===== TIME PARSER REPORT =====")
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    ))

    print("\n파일 생성 완료:")
    print(" - 03_time_periods.csv")
    print(" - 04_time_parse_review.csv")
    print(" - 02_time_parse_report.json")


if __name__ == "__main__":
    main()
