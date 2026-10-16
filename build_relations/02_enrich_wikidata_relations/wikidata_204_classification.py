"""Wikidata P31/P279 기반 엔티티 유형 분류."""
import re

from wikidata_201_config import (
    EVENT_INSTANCE_QIDS, HUMAN_QID, ORGANIZATION_INSTANCE_QIDS,
    PLACE_INSTANCE_QIDS, WORK_INSTANCE_QIDS,
)
from wikidata_202_client import claim_qids, fetch_wikidata_entities, labels_for_qids

def fetch_type_hierarchy(instance_qids, entities, max_depth=2):

          all_qids = set(instance_qids)

          frontier = set(instance_qids)


          for _ in range(max_depth):

                    parent_qids = set()


                    for qid in frontier:

                              parent_qids.update(

                                        claim_qids(entities.get(qid, {}), "P279")

                              )


                    new_qids = parent_qids - all_qids


                    if not new_qids:

                              break


                    entities.update(

                              fetch_wikidata_entities(sorted(new_qids))

                    )


                    all_qids.update(new_qids)

                    frontier = new_qids


          return list(all_qids)


def category_from_instance_of(instance_qids, instance_labels):

          qid_set = set(instance_qids)

          text = " ".join(instance_labels).casefold()


          if re.search(r"동음이의어|disambiguation", text):

                    return "unresolved"


          if re.search(

                    r"\b(award|prize)\b|수상|영화상|시상",

                    text,

          ):

                    return "award"


          if re.search(

                    r"\b(genre|style|emotion)\b|장르|양식|감정",

                    text,

          ):

                    return "genre"


          if HUMAN_QID in qid_set:

                    return "person"


          if qid_set & WORK_INSTANCE_QIDS:

                    return "work"


          if qid_set & PLACE_INSTANCE_QIDS:

                    return "place"


          if qid_set & ORGANIZATION_INSTANCE_QIDS:

                    return "organization"


          if qid_set & EVENT_INSTANCE_QIDS:

                    return "event"


          if re.search(

                    r"\b(event|battle|war|revolution|uprising)\b"

                    r"|사건|전쟁|전투|항쟁|혁명|해전",

                    text,

          ):

                    return "event"


          if re.search(

                    r"\b(film|television series|book|novel|webtoon|literary work)\b"

                    r"|영화|드라마|책|소설|웹툰|문학 작품",

                    text,

          ):

                    return "work"


          if re.search(

                    r"\b(city|country|village|geographic|location)\b"

                    r"|도시|국가|지역|지리",

                    text,

          ):

                    return "place"


          if re.search(

                    r"\b(organization|company|newspaper|broadcasting)\b"

                    r"|조직|기업|신문사|방송사",

                    text,

          ):

                    return "organization"


          return "unresolved"


def resolve_entity_category(qid, entities, classification_qids=None):

          """

          대상 엔티티의 P31과 P279 상위 타입을 이용해 분류한다.

          분류 결과와 함께 원본 P31, 분류에 사용한 QID 및 라벨을 반환한다.

          """

          wikidata_entity = entities.get(qid, {})


          if not qid or not wikidata_entity or wikidata_entity.get("missing"):

                    return {

                              "p31_qids": [],

                              "classification_qids": [],

                              "classification_labels": [],

                              "category": "unresolved",

                    }


          p31_qids = claim_qids(wikidata_entity, "P31")


          if classification_qids is None:
                    classification_qids = fetch_type_hierarchy(
                              p31_qids,
                              entities,
                              max_depth=2,
                    )
                    # 단독 호출 시에도 필요한 유형 엔티티를 확보한다.
                    entities.update(fetch_wikidata_entities(classification_qids))
          else:
                    classification_qids = list(classification_qids)

          # main()에서 미리 전달한 경우 해당 엔티티는 이미 일괄 조회되어 있다.


          classification_labels = labels_for_qids(

                    classification_qids,

                    entities,

          )


          category = category_from_instance_of(

                    classification_qids,

                    classification_labels,

          )


          return {

                    "p31_qids": p31_qids,

                    "classification_qids": classification_qids,

                    "classification_labels": classification_labels,

                    "category": category,

          }
