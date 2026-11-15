from wikidata_601_type_config import (
    AWARD_INSTANCE_QIDS,
    EVENT_INSTANCE_QIDS,
    GENRE_INSTANCE_QIDS,
    HUMAN_QID,
    NEXT_HOP_CATEGORIES,
    ORGANIZATION_INSTANCE_QIDS,
    PLACE_INSTANCE_QIDS,
    WORK_INSTANCE_QIDS,
)


def category_from_instance_of(
    instance_qids,
    all_type_qids,
    labels,
):
    """
    P31 및 P279 타입 계층을 바탕으로 엔티티 유형을 분류한다.

    반환:
        person, work, event, place, organization,
        award, genre, unresolved
    """
    all_qids = set(all_type_qids) | set(instance_qids)

    joined_labels = " ".join(
        labels.get(qid, "")
        for qid in all_qids
    ).casefold()

    if "동음이의어" in joined_labels:
        return "unresolved"

    if HUMAN_QID in all_qids:
        return "person"

    if all_qids & AWARD_INSTANCE_QIDS:
        return "award"

    if all_qids & GENRE_INSTANCE_QIDS:
        return "genre"

    if all_qids & PLACE_INSTANCE_QIDS:
        return "place"

    if all_qids & ORGANIZATION_INSTANCE_QIDS:
        return "organization"

    if all_qids & EVENT_INSTANCE_QIDS:
        return "event"

    if all_qids & WORK_INSTANCE_QIDS:
        return "work"

    # 대표 QID 집합으로 분류되지 않을 때 라벨을 보조적으로 사용한다.
    if any(word in joined_labels for word in (
        "텔레비전 시리즈",
        "영화 작품",
        "문학 작품",
        "소설 작품",
        "책",
        "도서",
        "written work",
        "literary work",
        "film",
        "television series",
    )):
        return "work"

    if any(word in joined_labels for word in (
        "전투",
        "전쟁",
        "역사적 사건",
        "사건",
        "battle",
        "war",
        "historical event",
    )):
        return "event"

    if any(word in joined_labels for word in (
        "도시",
        "국가",
        "지역",
        "지리적 위치",
        "city",
        "country",
        "geographic location",
    )):
        return "place"

    if any(word in joined_labels for word in (
        "출판사",
        "기업",
        "기관",
        "조직",
        "publisher",
        "company",
        "organization",
    )):
        return "organization"

    return "unresolved"


def is_next_hop_eligible(
    row,
    category,
    evidence,
    qid,
    qid_collision=False,
):
    """
    다음 홉 탐색을 허용할지 결정한다.

    DB에 동일 QID가 이미 존재하는 미검증 후보는
    분류 결과가 있더라도 다음 홉을 허용하지 않는다.
    """
    if not qid:
        return False

    if qid_collision:
        return False

    if category not in NEXT_HOP_CATEGORIES:
        return False

    if row["relationship_type"] == "wikipedia_mention_candidate":
        return False

    if evidence.get("mention_reason"):
        return False

    if evidence.get("relation_hint") == "comparison":
        return False

    return True