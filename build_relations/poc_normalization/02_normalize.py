import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

INPUT_PATH = (
    ROOT / "data/enrichment/normalization/candidates.json"
)

OUTPUT_DIR = ROOT / "data/enrichment/normalization"
OUTPUT_PATH = OUTPUT_DIR / "review.json"


def normalize_text(text: str) -> str:
    """
    문자열 비교를 위한 최소한의 정규화.

    - 유니코드 정규화
    - 앞뒤 공백 제거
    - 연속 공백 통합
    - 영문 소문자 변환

    의미를 변경하거나 단어를 삭제하지 않는다.
    """
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text.strip())

    return text.casefold()


def load_candidates():
    with INPUT_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return data["candidates"]


def find_exact_duplicates(candidates):
    """
    동일한 이름의 후보를 탐지한다.

    같은 이름이어도 자동으로 통합하지 않는다.
    """
    groups = defaultdict(list)

    for candidate in candidates:
        name = candidate.get("original_name") or ""
        normalized = normalize_text(name)

        if normalized:
            groups[normalized].append(candidate["candidate_id"])

    duplicates = []

    for normalized_name, candidate_ids in groups.items():
        if len(candidate_ids) < 2:
            continue

        duplicates.append({
            "normalized_name": normalized_name,
            "candidate_ids": candidate_ids,
            "count": len(candidate_ids),
        })

    return duplicates


def prepare_review(candidates):
    review_items = []

    for candidate in candidates:
        item = candidate.copy()

        eligible = item.get("eligible_for_review", False)
        flags = item.get("review_flags") or []

        if not eligible:
            # 출처·인용문 검증 실패 등
            item["decision"] = "hold"
            item["review_priority"] = "high"

        elif flags:
            # 검토 플래그가 있는 후보
            item["decision"] = "pending"
            item["review_priority"] = "high"

        else:
            item["decision"] = "pending"
            item["review_priority"] = "normal"

        review_items.append(item)

    return review_items


def main():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"입력 파일이 없습니다: {INPUT_PATH}"
        )

    if OUTPUT_PATH.exists():
        raise FileExistsError(
            f"이미 검토 파일이 존재합니다: {OUTPUT_PATH}\n"
            "기존 검토 결과를 보호하기 위해 덮어쓰지 않습니다."
        )

    candidates = load_candidates()

    review_items = prepare_review(candidates)

    duplicates = find_exact_duplicates(review_items)

    hold_count = sum(
        item["decision"] == "hold"
        for item in review_items
    )

    flagged_count = sum(
        bool(item.get("review_flags"))
        for item in review_items
    )

    result = {
        "created_at": datetime.now().astimezone().isoformat(),
        "source_file": str(INPUT_PATH.relative_to(ROOT)),
        "summary": {
            "total_candidates": len(review_items),
            "hold_count": hold_count,
            "flagged_count": flagged_count,
            "exact_duplicate_groups": len(duplicates),
        },
        "exact_duplicates": duplicates,
        "review_items": review_items,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("\n===== 정규화 검토 준비 완료 =====")
    print(f"전체 후보: {len(review_items)}개")
    print(f"검증 실패 보류: {hold_count}개")
    print(f"검토 플래그 보유: {flagged_count}개")
    print(f"동일 이름 중복 그룹: {len(duplicates)}개")
    print(f"저장: {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()