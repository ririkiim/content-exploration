import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

INPUT_PATH = ROOT / "data/enrichment/normalization/review.json"
OUTPUT_PATH = ROOT / "data/enrichment/normalization/normalized.json"


# 명확히 같은 개념이라고 확인된 표현만 등록한다.
# 왼쪽: 변형 표현 / 오른쪽: 대표 토픽명
#
# 현재는 비워둔다.
# 검증되지 않은 동의어를 임의로 통합하지 않기 위해서다.
SYNONYM_MAP = {
    # "장애로 인한 채용 배제": "장애인 고용 차별",
}


def normalize_text(text):
    """의미를 변경하지 않는 최소한의 문자열 정규화."""
    if not isinstance(text, str):
        return ""

    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text.strip())

    return text


def comparison_key(text):
    """동일 표현 비교용 키."""
    return normalize_text(text).casefold()


def load_candidates():
    with INPUT_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return data["review_items"]


def build_synonym_map():
    """동의어 사전의 표기를 정규화한다."""
    mapping = {}

    for variant, canonical in SYNONYM_MAP.items():
        key = comparison_key(variant)
        value = normalize_text(canonical)

        if not key or not value:
            raise ValueError("동의어 사전에 빈 표현이 있습니다.")

        if key in mapping and mapping[key] != value:
            raise ValueError(f"동의어 매핑 충돌: {variant}")

        mapping[key] = value

    return mapping


def normalize_candidates(candidates):
    synonym_map = build_synonym_map()

    normalized_items = []
    groups = defaultdict(list)

    for candidate in candidates:
        item = candidate.copy()

        original_name = item.get("original_name")
        clean_name = normalize_text(original_name)

        if not clean_name:
            item["normalization_status"] = "hold"
            item["normalization_reason"] = "empty_topic_name"
            item["canonical_name"] = None

        elif not item.get("eligible_for_review", False):
            item["normalization_status"] = "hold"
            item["normalization_reason"] = "evidence_validation_failed"
            item["canonical_name"] = None

        else:
            key = comparison_key(clean_name)

            canonical_name = synonym_map.get(key, clean_name)

            item["canonical_name"] = canonical_name
            item["normalization_status"] = "accepted"

            if key in synonym_map:
                item["normalization_reason"] = "synonym_mapping"
            elif canonical_name != original_name:
                item["normalization_reason"] = "text_cleanup"
            else:
                item["normalization_reason"] = "original_name"

            groups[comparison_key(canonical_name)].append(
                item["candidate_id"]
            )

        normalized_items.append(item)

    return normalized_items, groups


def build_topics(items, groups):
    """동일 대표 이름을 가진 후보들을 하나의 토픽으로 묶는다."""
    item_lookup = {
        item["candidate_id"]: item
        for item in items
    }

    topics = []

    for index, (_, candidate_ids) in enumerate(
        sorted(groups.items()), start=1
    ):
        first_item = item_lookup[candidate_ids[0]]

        topics.append({
            # DB의 최종 topic_id가 아닌 임시 식별자
            "temporary_topic_id": f"T{index:04d}",
            "canonical_name": first_item["canonical_name"],
            "candidate_ids": candidate_ids,
            "content_ids": sorted({
                item_lookup[cid]["content_id"]
                for cid in candidate_ids
            }),
            "candidate_count": len(candidate_ids),
        })

    return topics


def main():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"입력 파일이 없습니다: {INPUT_PATH}"
        )

    if OUTPUT_PATH.exists():
        raise FileExistsError(
            f"이미 결과 파일이 존재합니다: {OUTPUT_PATH}\n"
            "기존 결과를 보호하기 위해 덮어쓰지 않습니다."
        )

    candidates = load_candidates()

    items, groups = normalize_candidates(candidates)
    topics = build_topics(items, groups)

    accepted = sum(
        item["normalization_status"] == "accepted"
        for item in items
    )

    held = sum(
        item["normalization_status"] == "hold"
        for item in items
    )

    merged_groups = sum(
        topic["candidate_count"] > 1
        for topic in topics
    )

    result = {
        "created_at": datetime.now().astimezone().isoformat(),
        "source_file": str(INPUT_PATH.relative_to(ROOT)),
        "method": "rule_based",
        "summary": {
            "total_candidates": len(items),
            "accepted_candidates": accepted,
            "held_candidates": held,
            "unique_topics": len(topics),
            "merged_groups": merged_groups,
        },
        "topics": topics,
        "normalized_items": items,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n===== 규칙 기반 정규화 완료 =====")
    print(f"전체 후보: {len(items)}개")
    print(f"정규화 통과: {accepted}개")
    print(f"보류: {held}개")
    print(f"고유 토픽: {len(topics)}개")
    print(f"통합된 그룹: {merged_groups}개")
    print(f"저장: {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()