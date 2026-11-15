
"""규칙 기반 후보 점수 v2.
QID/P31 타입화 전의 보수적 임시 점수다.
"""

import re
from collections import Counter


SCORE_VERSION = "v2-rule"

EVENT = re.compile(
    r"(사건|전쟁|항쟁|혁명|작전|조약|해전|학살|운동|재란)$"
)
ERA = re.compile(
    r"(\d{4}년대|시대|일제강점기|왕조)$"
)
PLACE = re.compile(
    r"(특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면)$"
)
ORGANIZATION = re.compile(
    r"(방송|채널|위원회|엔터테인먼트|스튜디오|신문|일보)"
)
CAST_PERSON = re.compile(
    r"\([^)]*(배우|가수|성우)[^)]*\)$"
)
PERSON_HINT = re.compile(
    r"\([^)]*(감독|작가|소설가|만화가|무신|정치인)[^)]*\)$"
)


GROUP_BASE = {
    "narrative": 6,
    "context": 10,
    "adaptation": 9,
    "related": 5,
    "original_work": 9,
    "original_creator": 8,
    "series": 9,
    "genres": 6,
    "setting": 10,
    "related_work": 8,
    "creator": 8,
    "mention": 2,
}


def link_counts(wikitext, link_pattern, normalize):
    """위키텍스트에서 정규화된 링크 이름별 등장 횟수를 계산한다."""
    counts = Counter()

    for match in link_pattern.finditer(wikitext):
        name = normalize(match.group(1))

        # 비어 있는 링크 이름은 빈 문자열 키로 집계하지 않는다.
        if name:
            counts[name] += 1

    return counts


def entity_category(candidate):
    """후보의 탐색용 분류를 규칙 기반으로 판정한다."""
    name = candidate["name"]
    entity_type = candidate["entity_type"]

    # 배우·가수·성우 표기가 있는 인물을 먼저 판별한다.
    # entity_type이 person이어도 출연자 분류가 유지되도록 순서를 조정한다.
    if CAST_PERSON.search(name):
        return "cast_person"

    if entity_type == "source_work":
        return "work"

    if entity_type == "person":
        return "person"

    if PERSON_HINT.search(name):
        return "person"

    if EVENT.search(name):
        return "event"

    if ERA.search(name):
        return "era"

    if ORGANIZATION.search(name):
        return "organization"

    if PLACE.search(name):
        return "place"

    return "concept"


def score_candidates(candidates, counts, normalize):
    """후보별 점수·탐색 분기·다음 탐색 허용 여부를 계산한다."""

    for candidate in candidates:
        observations = candidate.get("observations", [])
        group = candidate["group"]

        # 그룹별 기본 점수를 우선 적용한다.
        source = GROUP_BASE.get(group, candidate["score"])

        excerpt = candidate.get("excerpt", "")

        # 작품 소개 첫머리에 단순히 개봉·방영·공개 정보로 등장한 링크는 감점한다.
        if (
            candidate["section"] == "lead"
            and excerpt.startswith("《")
            and re.search(r"(개봉|방영|공개)", excerpt)
        ):
            source = 3

        # 단순 언급으로 수집된 후보는 낮은 기본 점수를 적용한다.
        if candidate.get("mention_reason"):
            source = 2

        # 비교 언급이 아닌 명시적인 관계 힌트가 있으면 최소 점수를 보장한다.
        if (
            candidate.get("relation_hint")
            and candidate["relation_hint"] != "comparison"
        ):
            source = max(source, 8)

        category = entity_category(candidate)

        type_bonus = {
            "event": 3,
            "era": 3,
            "person": 2,
            "work": 2,
            "cast_person": 0,
            "place": -1,
            "organization": -4,
        }.get(category, 0)

        # 서로 다른 문서 구역에서 관찰된 경우에만 교차 구역 보너스를 준다.
        sections = sorted(
            {
                item["section"]
                for item in observations
            }
        )
        cross_bonus = min(2, max(0, len(sections) - 1))

        # 이름의 링크 등장 횟수를 계산한다.
        # 빈 이름은 link_counts()에서 제외했으므로 기본값 0을 사용한다.
        count = counts.get(normalize(candidate["name"]), 0)

        # 링크 빈도 보너스는 탐색에 유용할 가능성이 있는 분류에만 적용한다.
        # 배우·장소·기관은 반복 등장만으로 중요도를 높이지 않는다.
        frequency_bonus = (
            2 if count >= 6
            else 1 if count >= 3
            else 0
        ) if category in {"event", "era", "work", "person"} else 0

        final = max(
            0,
            source + type_bonus + cross_bonus + frequency_bonus
        )

        candidate["source_score"] = source
        candidate["rule_score"] = (
            type_bonus + cross_bonus + frequency_bonus
        )
        candidate["score"] = final

        candidate["score_features"] = {
            "source_score": source,
            "entity_category": category,
            "type_bonus": type_bonus,
            "sections": sections,
            "cross_bonus": cross_bonus,
            "link_count": count,
            "frequency_bonus": frequency_bonus,
            "score_version": SCORE_VERSION,
        }

        # 추천 결과에서 후보를 어떤 탐색 분기로 보여줄지 지정한다.
        if candidate["relation_type"] == "wikipedia_creator_candidate":
            exploration_branch = "creator"
        elif category == "cast_person":
            exploration_branch = "cast"
        elif category == "work":
            exploration_branch = "source_or_related_work"
        elif category in {"event", "era"}:
            exploration_branch = "history_context"
        elif category in {"place", "organization"}:
            exploration_branch = "place_or_organization"
        else:
            exploration_branch = "theme_context"

        candidate["exploration_branch"] = exploration_branch

        # 다음 단계 탐색은 특정 유형의 후보 중에서도
        # 단순 언급이나 비교 대상으로 수집된 후보에는 허용하지 않는다.
        eligible_categories = {
            "event",
            "era",
            "work",
            "person",
            "cast_person",
        }

        is_mention_candidate = (
            candidate["relation_type"] == "wikipedia_mention_candidate"
        )
        has_mention_reason = bool(candidate.get("mention_reason"))
        has_comparison_hint = (
            candidate.get("relation_hint") == "comparison"
        )

        candidate["next_hop_eligible"] = (
            category in eligible_categories
            and not is_mention_candidate
            and not has_mention_reason
            and not has_comparison_hint
        )

        candidate["score_features"]["next_hop_eligible"] = (
            candidate["next_hop_eligible"]
        )

    return candidates