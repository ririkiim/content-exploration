"""관계 구축 스크립트가 공통으로 쓰는 작은 유틸리티."""

import re
import unicodedata


def normalize_name(value: object) -> str:
    """한글뿐 아니라 모든 유니코드 문자권을 보존해 엔티티 비교 키를 만든다."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def require_normalized_name(value: object) -> str:
    normalized = normalize_name(value)
    if not normalized:
        raise ValueError(f"엔티티 이름을 정규화할 수 없습니다: {value!r}")
    return normalized