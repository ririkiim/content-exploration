"""PostgreSQL 콘텐츠 조회 및 엔티티/관계 저장."""
import json

from wikidata_201_config import FACT_STATUS, SOURCE_NAME, TYPE_SCORE_VERSION, VISIBILITY
from wikidata_202_client import claim_qids
from wikidata_203_matching import normalize_isbn, normalize_author
from wikidata_205_relations import merge_evidence

def load_contents(conn):

          """

          콘텐츠, 외부 소스 ID, ISBN, YES24 작가명을 조회한다.


          YES24 원본 연결:

          content_source_map.source_id = raw_yes24_item.yes24_item_id

          작가명은 raw_yes24_item.payload의 author 필드에서 가져온다.

          """

          rows = conn.execute(

                    """

                    SELECT

                              c.content_id,

                              c.content_type,

                              c.title,

                              c.description,

                              csm.source_name,

                              csm.source_id,

                              b.isbn10,

                              b.isbn13,

                              r.payload ->> 'author' AS raw_author

                    FROM content AS c

                    JOIN content_source_map AS csm

                         ON csm.content_id = c.content_id

                    LEFT JOIN book AS b

                         ON b.content_id = c.content_id

                    LEFT JOIN LATERAL (

                              SELECT

                                        raw.payload

                              FROM raw_yes24_item AS raw

                              WHERE raw.yes24_item_id::text = csm.source_id

                              ORDER BY

                                        raw.fetched_at DESC,

                                        raw.raw_id DESC

                              LIMIT 1

                    ) AS r

                              ON csm.source_name = 'yes24'

                    WHERE c.content_type IN ('movie', 'tv', 'book')

                         AND csm.source_name IN ('tmdb', 'yes24')

                    ORDER BY c.content_id

                    """

          ).fetchall()


          contents = {}


          for row in rows:

                    content_id = row["content_id"]


                    if content_id not in contents:

                              contents[content_id] = {

                                        "content_id": content_id,

                                        "content_type": row["content_type"],

                                        "title": row["title"],

                                        "description": row["description"],

                                        "source_ids": {},

                                        "isbn10": normalize_isbn(row["isbn10"]),

                                        "isbn13": normalize_isbn(row["isbn13"]),

                                        "author": None,

                              }


                    contents[content_id]["source_ids"][row["source_name"]] = (

                              str(row["source_id"])

                    )


                    if row["raw_author"]:

                              contents[content_id]["author"] = row["raw_author"]


          return list(contents.values())


def get_entity_description(entity):

          return (

                    entity.get("descriptions", {}).get("ko", {}).get("value")

                    or entity.get("descriptions", {}).get("en", {}).get("value")

          )


def get_entity_name(entity, qid):

          return (

                    entity.get("labels", {}).get("ko", {}).get("value")

                    or entity.get("labels", {}).get("en", {}).get("value")

                    or qid

          )


def save_entity(conn, qid, wikidata_entity, category):

          """

          QID가 이미 등록되어 있으면 해당 엔티티를 재사용한다.

          다른 엔티티가 같은 QID를 보유한 경우 중복 QID를 새로 할당하지 않는다.

          """

          row = conn.execute(

                    """

                    SELECT entity_id

                    FROM entity

                    WHERE qid = %s

                    ORDER BY entity_id

                    LIMIT 1

                    """,

                    (qid,),

          ).fetchone()


          if row:

                    entity_id = row["entity_id"]


                    conn.execute(

                              """

                              UPDATE entity

                              SET wikidata_instance_of = %s::jsonb,

                                        wikidata_category = %s,

                                        wikidata_resolved_at = NOW()

                              WHERE entity_id = %s

                              """,

                              (

                                        json.dumps(

                                                  claim_qids(wikidata_entity, "P31"),

                                                  ensure_ascii=False,

                                        ),

                                        category,

                                        entity_id,

                              ),

                    )


                    return entity_id


          entity_name = get_entity_name(wikidata_entity, qid)

          description = get_entity_description(wikidata_entity)


          # 같은 유형·이름의 엔티티가 있으면 먼저 재사용한다.
          row = conn.execute(

                    """

                    SELECT entity_id, qid

                    FROM entity

                    WHERE entity_type = %s

                         AND entity_name = %s

                    ORDER BY entity_id

                    LIMIT 1

                    """,

                    (category, entity_name),

          ).fetchone()


          if row:

                    entity_id = row["entity_id"]


                    # 기존 QID가 없고 다른 엔티티와 충돌하지 않을 때만 설정한다.
                    conn.execute(

                              """

                              UPDATE entity AS target

                              SET qid = %s

                              WHERE target.entity_id = %s

                                   AND target.qid IS NULL

                                   AND NOT EXISTS (

                                             SELECT 1

                                             FROM entity AS other

                                             WHERE other.qid = %s

                                                  AND other.entity_id <> %s

                                   )

                              """,

                              (qid, entity_id, qid, entity_id),

                    )


                    conn.execute(

                              """

                              UPDATE entity

                              SET wikidata_instance_of = %s::jsonb,

                                        wikidata_category = %s,

                                        wikidata_resolved_at = NOW()

                              WHERE entity_id = %s

                              """,

                              (

                                        json.dumps(

                                                  claim_qids(wikidata_entity, "P31"),

                                                  ensure_ascii=False,

                                        ),

                                        category,

                                        entity_id,

                              ),

                    )


                    return entity_id


          row = conn.execute(

                    """

                    INSERT INTO entity (

                              entity_type,

                              entity_name,

                              qid,

                              entity_description,

                              wikidata_instance_of,

                              wikidata_category,

                              wikidata_resolved_at

                    )

                    VALUES (

                              %s, %s, %s, %s, %s::jsonb, %s, NOW()

                    )

                    RETURNING entity_id

                    """,

                    (

                              category,

                              entity_name,

                              qid,

                              description,

                              json.dumps(

                                        claim_qids(wikidata_entity, "P31"),

                                        ensure_ascii=False,

                              ),

                              category,

                    ),

          ).fetchone()


          return row["entity_id"]


def save_relationship(

          conn,

          content_id,

          target_entity_id,

          relationship_type,

          property_id,

          source_qid,

          target_qid,

          claim_id,

          category,

          p31_qids,

          classification_qids,

          classification_labels,

          next_hop,

):

          """

          고유 제약조건에 해당하는 관계가 이미 있으면 근거를 병합한다.

          없으면 신규 관계를 저장한다.


          고유 제약조건:

          (source_type, source_id, relationship_type, target_type, target_id)


          source_name은 고유 제약조건에 포함되지 않으므로

          기존 관계 조회 조건에서도 제외한다.

          """


          row = conn.execute(

                    """

                    SELECT relationship_id, evidence

                    FROM relationship

                    WHERE source_type = 'content'

                         AND source_id = %s

                         AND relationship_type = %s

                         AND target_type = 'entity'

                         AND target_id = %s

                    LIMIT 1

                    """,

                    (

                              content_id,

                              relationship_type,

                              target_entity_id,

                    ),

          ).fetchone()


          new_source = {

                    "source_name": SOURCE_NAME,

                    "source_qid": source_qid,

                    "target_qid": target_qid,

                    "property_id": property_id,

                    "claim_id": claim_id,

          }


          new_evidence = {

                    "sources": [new_source],

                    "wikidata_qid": target_qid,

                    "wikidata_instance_of": p31_qids,

                    "wikidata_type_classification_qids": classification_qids,

                    "wikidata_instance_labels": classification_labels,

                    "wikidata_category": category,

                    "type_source": "wikidata_p31",

                    "next_hop_eligible": next_hop,

                    "type_score_version": TYPE_SCORE_VERSION,

          }


          if row:

                    # 기존 근거를 유지하면서 Wikidata 근거를 병합한다.
                    evidence = merge_evidence(

                              row["evidence"],

                              new_evidence,

                    )


                    conn.execute(

                              """

                              UPDATE relationship

                              SET evidence = %s::jsonb,

                                        next_hop_eligible = %s

                              WHERE relationship_id = %s

                              """,

                              (

                                        json.dumps(evidence, ensure_ascii=False),

                                        next_hop,

                                        row["relationship_id"],

                              ),

                    )


                    return "updated"


          # 기존 관계가 없을 때만 INSERT한다.
          conn.execute(

                    """

                    INSERT INTO relationship (

                              source_type,

                              source_id,

                              relationship_type,

                              target_type,

                              target_id,

                              source_name,

                              fact_status,

                              evidence,

                              visibility,

                              next_hop_eligible

                    )

                    VALUES (

                              'content',

                              %s,

                              %s,

                              'entity',

                              %s,

                              %s,

                              %s,

                              %s::jsonb,

                              %s,

                              %s

                    )

                    """,

                    (

                              content_id,

                              relationship_type,

                              target_entity_id,

                              SOURCE_NAME,

                              FACT_STATUS,

                              json.dumps(new_evidence, ensure_ascii=False),

                              VISIBILITY,

                              next_hop,

                    ),

          )


          return "inserted"
