"""원문은 유지하고 표기 정규화와 비교 후보만 기록한다."""
import re
import unicodedata

# 의미 유사어는 자동 등록하지 않는다. 검토를 마친 명백한 동의어만 추가한다.
APPROVED_ALIASES = {}


def normalization_candidate(topic):
    original = topic["name"]
    surface = " ".join(unicodedata.normalize("NFKC", original).split())
    canonical = APPROVED_ALIASES.get(surface, surface)
    return {"original_name": original, "candidate_name": canonical,
            "comparison_key": re.sub(r"\s+", "", canonical).casefold(),
            "type": topic["type"], "generation_path": "code_surface_normalization",
            "review_status": "proposed", "applied": False,
            "rule": "approved_alias" if canonical != surface else "unicode_and_spacing",
            "note": "같은 비교 키도 동일 개념 확정이나 자동 병합을 의미하지 않음"}


def compare_candidates(existing, incoming):
    old = [normalization_candidate(t) for t in existing]
    return [{"new": normalization_candidate(t), "existing_candidates": [p for p in old
             if p["comparison_key"] == normalization_candidate(t)["comparison_key"]
             and p["type"] == t["type"]], "review_status": "proposed", "merged": False}
            for t in incoming]


def interpretation_review_flags(topic):
    # 문자열 단서는 검토 우선순위일 뿐 과잉 해석 판정이 아니다.
    cues = ("의존", "저항", "갈등", "충돌", "긴장", "정체성", "윤리", "상징")
    flags = []
    if any(c in topic["name"] and c not in topic["evidence_quote"] for c in cues):
        flags.append("topic_label_strength_review")
        if topic["evidence_level"] == "explicit":
            flags.append("explicit_interpretation_review")
    return flags
