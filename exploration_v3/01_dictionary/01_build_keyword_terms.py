import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

INPUT = BASE_DIR / "content_keyword_export.csv"
OUTPUT = BASE_DIR / "staging"
OUTPUT.mkdir(exist_ok=True)

df = pd.read_csv(INPUT)

# 원본 정규화 표현을 그대로 사용
terms = (
    df[["keyword_normalized"]]
    .drop_duplicates()
    .sort_values("keyword_normalized")
    .reset_index(drop=True)
    .rename(columns={"keyword_normalized": "normalized_term"})
)

# 임시 ID: 현재 스테이징 파일 안에서만 사용
terms.insert(0, "staging_id", range(1, len(terms) + 1))

# 원본 키워드와 표현 사전 연결
mapping = df.merge(
    terms,
    left_on="keyword_normalized",
    right_on="normalized_term",
    how="left",
    validate="many_to_one",
)

mapping = mapping[
    ["keyword_id", "content_id", "source_name", "staging_id"]
]

# 검증
assert len(mapping) == len(df)
assert mapping["staging_id"].notna().all()
assert terms["normalized_term"].is_unique
assert mapping["keyword_id"].is_unique

terms.to_csv(OUTPUT / "01_keyword_terms.csv", index=False)
mapping.to_csv(OUTPUT / "02_keyword_term_map.csv", index=False)

print(f"표현 사전: {len(terms):,}개")
print(f"원본 매핑: {len(mapping):,}건")
print("스테이징 검증 완료")
