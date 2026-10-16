"""콘텐츠 Wikidata claim에서 관계 추출 및 관계 판정."""
from wikidata_201_config import CLAIM_RULES, CONTENT_CLAIMS, NEXT_HOP_CATEGORIES

def extract_relationships(content, qid, entities):

          """

          Wikidata의 지정된 속성에서 대상 QID를 추출한다.

          관계 대상의 실제 entity_type은 P31/P279 분류 결과로 결정한다.

          """

          wikidata_entity = entities.get(qid, {})


          if (

                    not qid

                    or not wikidata_entity

                    or wikidata_entity.get("missing")

          ):

                    return []


          content_type = content["content_type"]

          property_ids = CONTENT_CLAIMS.get(content_type, ())

          extracted = []


          for property_id in property_ids:

                    relationship_type, _expected_type = CLAIM_RULES[property_id]


                    for claim in wikidata_entity.get("claims", {}).get(property_id, []):

                              mainsnak = claim.get("mainsnak", {})


                              if mainsnak.get("snaktype") != "value":

                                        continue


                              value = mainsnak.get("datavalue", {}).get("value", {})

                              target_qid = value.get("id")


                              if not target_qid:

                                        continue


                              extracted.append({

                                        "property_id": property_id,

                                        "relationship_type": relationship_type,

                                        "target_qid": target_qid,

                                        "claim_id": claim.get("id"),

                              })


          return extracted


def is_next_hop_eligible(

          relationship_type,

          category,

          evidence,

):

          if category not in NEXT_HOP_CATEGORIES:

                    return False


          # 02에서 추출하는 관계는 언급 후보가 아니지만,
          # 근거에 해당 문맥 정보가 있다면 06과 같은 정책을 적용한다.
          if relationship_type == "wikipedia_mention_candidate":

                    return False


          if evidence.get("mention_reason"):

                    return False


          if evidence.get("relation_hint") == "comparison":

                    return False


          return True


def merge_evidence(existing_evidence, new_evidence):

          """

          기존 evidence.sources 및 기타 키를 보존하면서

          새 Wikidata 근거를 병합한다.

          """

          if isinstance(existing_evidence, dict):

                    evidence = dict(existing_evidence)

          elif existing_evidence is None:

                    evidence = {}

          else:

                    evidence = {"previous_evidence": existing_evidence}


          old_sources = evidence.get("sources", [])


          if not isinstance(old_sources, list):

                    old_sources = [old_sources] if old_sources else []


          new_sources = new_evidence.get("sources", [])


          for source in new_sources:

                    if source not in old_sources:

                              old_sources.append(source)


          evidence["sources"] = old_sources


          for key, value in new_evidence.items():

                    if key != "sources":

                              evidence[key] = value


          return evidence
