# 기존 DB 구조로 키워드 탐색 네트워크를 담을 수 있는가 (검토만, DB 미변경)
근거: `sql/serving/schema_serving.sql`, `DESIGN_entity_integration_v0.5.md`

| 필요한 것 | 기존 구조 | 판단 |
|---|---|---|
| 키워드 노드 | `entity`(entity_type에 KEYWORD/TOPIC 있음, wikidata_qid 부분 유니크) | 가능. 현재 100행뿐이라 키워드 약 1.9만 개는 신규 적재 |
| EXACT(동일 표기·번역) | `entity_alias`(kind: label/synonym/translation/seed, status proposed/verified/rejected) | 가능. 병합 컬럼은 없고 alias로 묶는 설계(v0.5)와 일치 |
| 키워드↔작품 | 원본 `content_keyword`(12.5만 행) + `content_entity`(근거 `db_keyword`+keyword_id) | 가능. 단 link_role에 'keyword' 값이 없어 `subject` 등을 빌려야 함 |
| 키워드↔키워드 관계 | `entity_relation`: BROADER / RELATED / TEMPORAL_WITHIN / LOCATED_IN, basis=source_fact\|semantic_inference | 부분 가능. Wikidata P279는 BROADER(source_fact), 나머지는 RELATED(semantic_inference) |
| 관계 강도(가중치) | 없음 | **부족**. 현재 구조로는 유형·basis·evidence_status 정도의 거친 등급만 표현 |
| 관계 세부 유형(번역/파생/불확실) | 없음(4종 고정) | **부족**. source_ref/policy_version 문자열에 넣을 수는 있으나 쿼리·점수화에 부적합 |
| 자동 생성 관계의 탐색 반영 | `next_hop_eligible`는 fact_status=verified AND evidence_status=checked일 때만 true(CHECK 제약) | **충돌**. 규칙 기반 자동 엣지를 verified/checked로 둘지(정책 버전으로 승인한 것으로 간주) 정해야 함 |
| 전체 저장 후 탐색 시 제한 | 가능(제한은 쿼리 단계) | 문제 없음 |

최소 변경안(적용하지 않음): `entity_relation`에 `score numeric`, `relation_subtype text` 추가 + 자동 엣지의 verified/checked 의미 정의(예: policy_version 승인). 변경 없이 쓰려면 강도는 유형·basis 3~4등급으로 단순화해야 함.
