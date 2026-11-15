"""Wikidata 관계 보강 파이프라인 실행 진입점."""
from collections import defaultdict

import psycopg
from psycopg.rows import dict_row

from wikidata_201_config import CACHE_DIR, DATABASE_URL
from wikidata_202_client import claim_qids, fetch_wikidata_entities
from wikidata_203_matching import match_content_qids
from wikidata_204_classification import fetch_type_hierarchy, resolve_entity_category
from wikidata_205_relations import extract_relationships, is_next_hop_eligible
from wikidata_206_repository import load_contents, save_entity, save_relationship

def main():

          CACHE_DIR.mkdir(parents=True, exist_ok=True)


          stats = defaultdict(int)


          with psycopg.connect(

                    DATABASE_URL,

                    row_factory=dict_row,

          ) as conn:

                    contents = load_contents(conn)


                    if not contents:

                              print("Wikidata 관계 추출 대상 콘텐츠가 없습니다.")

                              return


                    print("=== 콘텐츠 QID 매칭 ===")

                    print(f"대상 콘텐츠: {len(contents)}")


                    content_matches, match_stats = match_content_qids(contents)


                    for key, value in sorted(match_stats.items()):

                              print(f"{key}: {value}")


                    matched_qids = [

                              item["qid"]

                              for item in content_matches.values()

                              if item["qid"]

                    ]


                    entities = fetch_wikidata_entities(matched_qids)


                    # 콘텐츠 QID가 확인된 작품의 관계를 수집한다.
                    pending_relationships = []


                    for content in contents:

                              content_id = content["content_id"]

                              match = content_matches[content_id]

                              source_qid = match["qid"]


                              if not source_qid:

                                        stats["content_without_qid"] += 1

                                        continue


                              source_entity = entities.get(source_qid, {})


                              if (

                                        not source_entity

                                        or source_entity.get("missing")

                              ):

                                        stats["source_entity_unavailable"] += 1

                                        continue


                              extracted = extract_relationships(

                                        content,

                                        source_qid,

                                        entities,

                              )


                              stats["claims_extracted"] += len(extracted)


                              for item in extracted:

                                        pending_relationships.append({

                                                  "content": content,

                                                  "source_qid": source_qid,

                                                  **item,

                                        })


                    # 관계 대상 엔티티를 일괄 조회한다.
                    target_qids = list(dict.fromkeys(

                              item["target_qid"]

                              for item in pending_relationships

                    ))


                    entities.update(fetch_wikidata_entities(target_qids))


                    # P31 타입을 일괄 수집한다.
                    all_p31_qids = []


                    for target_qid in target_qids:

                              target = entities.get(target_qid, {})


                              if not target.get("missing"):

                                        all_p31_qids.extend(

                                                  claim_qids(target, "P31")

                                        )


                    all_p31_qids = list(dict.fromkeys(all_p31_qids))

                    entities.update(fetch_wikidata_entities(all_p31_qids))


                    # P31/P279 계층은 아래 classification_qids 수집 루프에서 한 번만 조회한다.

                    # P31/P279 계층은 대상 QID마다 한 번만 조회하고 결과를 재사용한다.
                    classification_qids = set()
                    classification_qids_by_target = {}

                    for target_qid in target_qids:
                              target = entities.get(target_qid, {})
                              if not target or target.get("missing"):
                                        continue

                              p31_qids = claim_qids(target, "P31")
                              type_qids = fetch_type_hierarchy(
                                        p31_qids,
                                        entities,
                                        max_depth=2,
                              )
                              classification_qids_by_target[target_qid] = type_qids
                              classification_qids.update(type_qids)

                    entities.update(
                              fetch_wikidata_entities(sorted(classification_qids))
                    )

                    # 동일한 대상 QID가 여러 관계에 등장해도 유형 분류는 한 번만 수행한다.
                    category_by_qid = {}
                    for target_qid in target_qids:
                              target = entities.get(target_qid, {})
                              if target and not target.get("missing"):
                                        category_by_qid[target_qid] = resolve_entity_category(
                                                  target_qid,
                                                  entities,
                                                  classification_qids=classification_qids_by_target.get(target_qid, []),
                                        )

                    print("=== 관계 저장 ===")

                    print(f"추출된 관계 후보: {len(pending_relationships)}")


                    for item in pending_relationships:

                              target_qid = item["target_qid"]

                              target_entity = entities.get(target_qid, {})


                              if (

                                        not target_entity

                                        or target_entity.get("missing")

                              ):

                                        stats["target_entity_unavailable"] += 1

                                        continue


                              type_result = category_by_qid.get(
                                        target_qid,
                                        {
                                                  "category": "unresolved",
                                                  "p31_qids": [],
                                                  "classification_qids": [],
                                                  "classification_labels": [],
                                        },
                              )


                              category = type_result["category"]

                              p31_qids = type_result["p31_qids"]

                              type_qids = type_result["classification_qids"]

                              type_labels = type_result["classification_labels"]


                              target_entity_id = save_entity(

                                        conn,

                                        target_qid,

                                        target_entity,

                                        category,

                              )


                              next_hop = is_next_hop_eligible(

                                        item["relationship_type"],

                                        category,

                                        {},

                              )


                              result = save_relationship(

                                        conn=conn,

                                        content_id=item["content"]["content_id"],

                                        target_entity_id=target_entity_id,

                                        relationship_type=item["relationship_type"],

                                        property_id=item["property_id"],

                                        source_qid=item["source_qid"],

                                        target_qid=target_qid,

                                        claim_id=item["claim_id"],

                                        category=category,

                                        p31_qids=p31_qids,

                                        classification_qids=type_qids,

                                        classification_labels=type_labels,

                                        next_hop=next_hop,

                              )


                              stats[result] += 1

                              stats[f"category_{category}"] += 1


                    # 정상 종료 시 psycopg 컨텍스트가 커밋한다.
          print()

          print("=== Wikidata 관계 보강 완료 ===")

          print(f"콘텐츠 QID 미확인: {stats['content_without_qid']}")

          print(f"원본 Wikidata 엔티티 조회 실패: {stats['source_entity_unavailable']}")

          print(f"추출한 Wikidata 관계: {stats['claims_extracted']}")

          print(f"대상 엔티티 조회 실패: {stats['target_entity_unavailable']}")

          print(f"신규 관계: {stats['inserted']}")

          print(f"기존 관계 근거 갱신: {stats['updated']}")


          print()

          print("=== 대상 엔티티 유형 ===")


          for category in (

                    "person",

                    "event",

                    "work",

                    "place",

                    "organization",

                    "genre",

                    "award",

                    "unresolved",

          ):

                    print(f"{category}: {stats[f'category_{category}']}")


          print()

          print(

                    "다음 홉은 person·event·work 중에서도 "

                    "언급 후보 및 비교 문맥을 제외한 관계만 허용합니다."

          )


if __name__ == "__main__":
    main()
