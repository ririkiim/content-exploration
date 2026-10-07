"""규칙 기반 후보 점수 v2. QID/P31 타입화 전의 보수적 임시 점수다."""
import re
from collections import Counter

SCORE_VERSION = "v2-rule"
EVENT = re.compile(r"(사건|전쟁|항쟁|혁명|작전|조약|해전|학살|운동|재란)$")
ERA = re.compile(r"(\d{4}년대|시대|일제강점기|왕조)$")
PLACE = re.compile(r"(특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면)$")
ORGANIZATION = re.compile(r"(방송|채널|위원회|엔터테인먼트|스튜디오|신문|일보)")
CAST_PERSON = re.compile(r"\([^)]*(배우|가수|성우)[^)]*\)$")
PERSON_HINT = re.compile(r"\([^)]*(감독|작가|소설가|만화가|무신|정치인)[^)]*\)$")

GROUP_BASE = {
    "narrative": 6, "context": 10, "adaptation": 9, "related": 5,
    "original_work": 9, "original_creator": 8, "series": 9,
    "genres": 6, "setting": 10, "related_work": 8,
    "creator": 8, "mention": 2,
}

def link_counts(wikitext, link_pattern, normalize):
    counts = Counter()
    for match in link_pattern.finditer(wikitext):
        counts[normalize(match.group(1))] += 1
    return counts

def entity_category(candidate):
    name = candidate["name"]
    if candidate["entity_type"] == "source_work": return "work"
    if candidate["entity_type"] == "person": return "person"
    if CAST_PERSON.search(name): return "cast_person"
    if PERSON_HINT.search(name): return "person"
    if EVENT.search(name): return "event"
    if ERA.search(name): return "era"
    if ORGANIZATION.search(name): return "organization"
    if PLACE.search(name): return "place"
    return "concept"

def score_candidates(candidates, counts, normalize):
    for candidate in candidates:
        observations = candidate.get("observations", [])
        group = candidate["group"]
        source = GROUP_BASE.get(group, candidate["score"])
        excerpt = candidate.get("excerpt", "")
        if candidate["section"] == "lead" and excerpt.startswith("《") and re.search(r"(개봉|방영|공개)", excerpt):
            source = 3
        if candidate.get("mention_reason"):
            source = 2
        if candidate.get("relation_hint") and candidate["relation_hint"] != "comparison":
            source = max(source, 8)

        category = entity_category(candidate)
        type_bonus = {"event": 3, "era": 3, "person": 2, "work": 2,
                      "cast_person": 0, "place": -1,
                      "organization": -4}.get(category, 0)
        sections = sorted({item["section"] for item in observations})
        cross_bonus = min(2, max(0, len(sections) - 1))
        count = counts.get(normalize(candidate["name"]), 0)
        # 화면에 자주 보인다고 배우·장소·기관이 중요한 탐색 seed가 되지는 않는다.
        frequency_bonus = (
            2 if count >= 6 else 1 if count >= 3 else 0
        ) if category in {"event", "era", "work", "person"} else 0
        final = max(0, source + type_bonus + cross_bonus + frequency_bonus)
        candidate["source_score"] = source
        candidate["rule_score"] = type_bonus + cross_bonus + frequency_bonus
        candidate["score"] = final
        candidate["score_features"] = {"source_score": source, "entity_category": category,
            "type_bonus": type_bonus, "sections": sections, "cross_bonus": cross_bonus,
            "link_count": count, "frequency_bonus": frequency_bonus,
            "score_version": SCORE_VERSION}
        candidate["exploration_branch"] = (
            "creator" if candidate["relation_type"] == "wikipedia_creator_candidate"
            else "cast" if category == "cast_person"
            else "source_or_related_work" if category == "work"
            else "history_context" if category in {"event", "era"}
            else "place_or_organization" if category in {"place", "organization"}
            else "theme_context"
        )
        candidate["next_hop_eligible"] = category in {
            "event", "era", "work", "person", "cast_person"
        }
        candidate["score_features"]["next_hop_eligible"] = candidate["next_hop_eligible"]
    return candidates
