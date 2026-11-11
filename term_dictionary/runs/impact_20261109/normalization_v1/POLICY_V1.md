# 키워드 정규화 v1 — 처리 기준과 건수 (2026-11-11)
기존 결과만 사용(신규 LLM/Wikidata 조회·DB 변경 없음). 건수는 `v1_summary.json`, 쌍별 결정은 `v1_decisions_nonhold.csv`, 판정 원장은 `ledger/verified_pairs.csv`.
후보 쌍 모집단: 308,625 (grade_rules_v0.1 산출 merge_proposals). 상태 의미: `llm_only`/`rule_auto`/`human_verified`는 **판정 출처**이고, DB 반영 상태(`db_state`)는 전부 `not_applied`.

## 1. 코드로 확정 (CODE) — 총 1,864쌍 (SAME 53, NOT_SAME 1,811)
| 구분 | 기준 | 건수 |
|---|---|---|
| CODE_SAME | A 규칙: 정규화 후 동일 문자열(띄어쓰기·대소문자·구두점 차이). 다의성 약어 6종은 제외하고 LLM으로 | 45 |
| CODE_SAME | 숫자 단위 정합 번역: N년대↔Ns, N세기↔Nth century | 8 |
| CODE_NOT_SAME | 정규화 후 숫자 집합이 충돌(1980년대 vs 1990s 등), 서로 포함하지 않음 | 1,778 |
| CODE_NOT_SAME | 서로 다른 기존 엔티티에 이미 속함 | 33 |
- 자동 SAME은 53쌍이며, 후보 대비 매우 작음. E5 점수·공출현·Wikidata는 자동 SAME 근거로 쓰지 않음(실험에서 안전한 임계값 없음).

## 2. LLM으로 넘김 (LLM_REVIEW) — 1,808쌍
후보 선정 기준(재현율 우선, 공출현은 필수 아님): 기존 C 등급(다중 신호, 585) + 다의성 약어 6 + v0.3 승격 한영 H1 상호 top1 비허브(1,217; 숫자단위 8쌍은 코드 확정) + 표기 변형 후보(23). 승인은 양방향 SAME일 때만 후보.
| 현재 상태 | 건수 | 의미 |
|---|---|---|
| LLM_SAME_ONLY | 561 | 양방향 SAME. 출처 `llm_only`, 사람 표본 미확인(65쌍 표본 시트 미기입). DB 미반영 |
| LLM_NOT_SAME | 476 | 양방향 NOT_SAME |
| LLM_HELD | 176 | UNSURE 또는 양방향 불일치 → 자동 승인 금지, 보류 |
| HUMAN_SAME / NOT_SAME / UNSURE | 35 / 4 / 3 | 사람 판정이 LLM을 덮어씀(충돌 3건 기록) |
| LLM_PENDING | 553 | 아직 LLM 미호출. 1,106 요청, v3 실측 단가로 약 $12. 대부분 기존 C 후보(C2 160, C3 256, C1 50, C4 허브 62 등) |
- LLM_SAME_ONLY 561 중 548은 `candidate_for_proposed_alias`(양방향 일치, 다의성 플래그 없음, df<100), 13은 일반어/플래그 때문에 `needs_review_generic_or_flagged`. DB 반영(proposed alias)은 별도 정책 결정 사항이며 이번에 적용하지 않음.

## 3. 보류 (HOLD, 거절 아님) — 304,953쌍
검토 경로가 없는 후보. 단일 신호(한영 148,346 / 동일 한국어 93,440 / 동일 영어 31,513), 허브 29,151, 동일 언어 H1 미승격(한국어 1,869 / 영어 634). 블라인드 평가(60쌍)에서 SAME 비율이 낮았거나(0~10%) 근거가 없어 v1에서는 처리하지 않음. NOT_SAME으로 취급하지 않음. 후보 생성 재현율(E5 top10 밖)은 미측정.

## 4. 보조 근거 (결정하지 않음)
- 콘텐츠 단위 공출현(shared≥1): 한영 H1 98쌍에서 SAME 87/UNSURE 10/NOT_SAME 1(대법원/lawyer). 자동 승인 금지, `assist_cooc_shared` 열로만 제공(LLM 입력·표본 우선순위용). E5 점수 임계값은 규칙에 넣지 않음.
- Wikidata 캐시: 용어 3,096 중 919 검색, 후보 쌍 1,816의 근거표 `wikidata_evidence/pair_wikidata_evidence.csv`. 사용은 LLM 맥락·사람 검토 참고용(context_only). **검색 실패, 미검색, 서로 다른 QID는 NOT_SAME 근거가 아님**: 파일럿에서 `different_exact_qids` 쌍도 SAME이 13/64(20%), 검색 실패 쌍도 SAME이 28/88. 추가 Wikidata 조회는 중단.
- 완화 B(개념형 경쟁 QID만 계산): 탐색적 결과로 `policy_eval/qid_relaxed_*` 보존, 정책 미반영(24쌍 통과, 순증 15쌍, 사후 정의).

## 5. 원장 (pair_key 영구 재사용)
- 구현: `ledger_lib.py`(조회/추가/현재 뷰), `build_ledger.py`(기존 결과 1회 이관, 덮어쓰기 거부). 원장 3,232행(llm_only 1,271 / rule_auto 1,864 / human_verified 89 / superseded 8), 현재 뷰 3,174쌍.
- 우선순위 human_verified > rule_auto > llm_only. 판정이 다르면 충돌로 표시(현재 3건: ai, la, minority/미성년자, 사람 판정이 우선). `needs_llm(pairs)`로 판정이 없는 쌍만 다음 LLM 입력으로 추려 재호출을 막음. UNSURE도 판정 보유로 간주(근거 변경 시 재큐잉은 미구현).
- 원장은 판정 출처 기록이며 DB 승인이 아님.

## 6. 한계와 남은 결정
- LLM_PENDING 553쌍 호출 여부($12), 65쌍 사람 표본 확인, LLM_SAME_ONLY의 DB proposed alias 반영 정책(일반어 13, 허브 위험 포함)은 미결.
- LLM 판정은 잠정 라벨이고 표본 확인 전. v1의 합집합은 모집단 306k 중 약 3.7k쌍만 다룸.
