# dictionary_v2 v1.0 진행 현황 및 향후 로직 업데이트

## 0. 세 줄 요약

1. 영화·드라마의 서로 다른 키워드를 증거 기반으로 연결하는 사전을 구축했다. 키워드 관계의 존재와 작품 추천의 적합성은 **구별**한다.
2. 최초 신규 작품 연결 2,979,048쌍을 측정했으나, FORM 누락 수정 후 2,745,494쌍으로 감소했다. **현재 최신 측정값이지 최종 품질 보장 수치는 아니다.**
3. 앞으로는 **역사/시대 충돌 → 국가·위치 태그 → 기존 METADATA 보완 → 작품 쌍 재산출 → 실제 추천 점수/품질 평가** 순으로 진행한다.

## 1. 목적과 반드시 유지할 원칙

- 프로젝트: 영화·드라마·도서 간 키워드/개념 관계를 이용해 탐색 경로 제공. 현재 v1 실험은 **영화와 드라마만** 포함; 도서는 적재/정제 후 확장.
- 단어를 무조건 SAME으로 합치거나 '완전 동일한 개념'임을 증명하려는 프로젝트가 아님. **서로 다른 표현·개념을 보존**하면서 출처와 근거가 있는 관련 관계를 부여.
- DB/규칙/근거 기반 처리 중심. LLM은 보조 평가·설명에 제한적으로 활용하며 모든 관계의 운영상 필수 판정자가 아님.
- 기존 근거는 삭제하지 않고 개념 역할 및 `usage_scope`로 **일반 탐색에서 사용할지** 구분.
- `rule_id`·`weight`는 선정 규칙과 상대적 우선순위이며 **정답 확률이 아니다**. 특히 `R1=0.95`를 '95% 정확'으로 표현하지 말 것.
- '의미 관계로 연결 가능한 작품 쌍'과 '사용자에게 추천하기 좋은 작품 쌍'은 다름. 신규 연결 수를 추천 정확도/품질로 주장하지 않음.
- 임의로 규칙이나 임계값을 사후 변경해 기존 샘플의 성능을 부풀리지 않음. 검증 표본은 규칙·영향 규모를 나누어 확인.

## 2. 현재 데이터 규모 및 실제 확인된 상태

### 키워드/관계

- 전체 개념 수: **19,443개**.
- `dictionary_v2.concept_pair`: 후보 **469,717개**.
  - Jina 상호 임베딩 및 KMDb↔TMDb 교차 출처 공동 출현 근거. Jina만 420,428 / cross만 46,970 / 양쪽 2,319.
- `dictionary_v2.concept_relation_v1_candidate`: 최종 선정 후보 **40,640개**.
- `dictionary_v2.concept_relation_v1`: 현재 40,640개 전체 보존. 개념 역할 및 탐색 범위 적용됨.
- `dictionary_v2.concept_role_v1`: **FORM 164 / SETTING 58 / TOPIC 19,221**, 총 19,443. 초기 FORM 154에서 `remade`, `live action remake`, `horror remake`, `web series`, `webserie` 5개를 추가해 159; 이후 `단편영화`, `독립영화`, `documentary short`, `independent film`, `short film` 5개를 추가해 164.
- 현재 관계의 범위별 수:
  - `GENERAL` 40,223 = `TOPIC_TOPIC` 39,795 + `SETTING_TOPIC` 292 + `SETTING_SETTING` 136.
  - `FORMAT` `FORM_FORM` 103.
  - `EXCLUDED` `FORM_OTHER` 314.
  - 총합 40,640. FORM 누락 수정 전 `GENERAL` 40,251 / `FORMAT` 96 / `EXCLUDED` 293.
- **`단편영화 ↔ short film` = R1 0.95 / `FORM_FORM` / `FORMAT` 확인 완료.**

### 작품

- `public.content`: 영화 약 39,715편, 드라마 2,963편, 도서 923편 존재하지만 이 모두가 개념을 가진 분석 대상은 아님.
- 실제 `dictionary_v2.content_concept_v1` 평가 대상: **영화 18,318편, 드라마 2,033편**, 총 20,351편.
- 작품↔개념 중복 제거 연결: 영화 **112,826건**, 드라마 **12,084건**, 합계 **124,910건**.
- 작품당 개념 평균: 영화 **6.16개** (1~67), 드라마 **5.94개** (1~91).
- 영화·드라마에 매핑되지 않은 `content_term` 행은 0건이었음. 동일 작품이 KMDb/TMDb에 있더라도 `content_id` 기준 중복 제거.

### 효과 측정 (수정 전/후 구분 필수)

- 최초 예상 '관계 경로' 계산: 영화↔영화 4,286,226 / 영화↔드라마 2,140,750 / 드라마↔드라마 35,554 = 총 **6,462,530**. 이는 **개념별 작품 수를 곱한 중복 제거 전 경로 수**로, 영화↔영화 및 드라마↔드라마의 원래 SQL은 역방향 항을 더해 2배 셌음. 최종 고유 작품 쌍 수가 아님.
- 동일 개념 공유를 기준선으로 삼고, 새로운 다른 개념 간 관계를 통과한 작품 쌍에서 중복과 기존 쌍을 제거한 최초 신규 작품 쌍: **2,979,048**.
  - 영화↔영화 **1,373,729** / 영화↔드라마 **1,597,575** / 드라마↔드라마 **7,744**.
- FORM 5개 보완 후 현재 `GENERAL` 기준으로 재계산한 **신규 작품 쌍 합계 2,745,494**.
  - 이전 대비 **233,554쌍 감소** (약 7.84%).
  - 이 233,554쌍을 '전부 잘못된 추천'이라고 부르지 말 것. 일반 탐색에서 형식 근거만으로 연결된 쌍 등이 빠진 결과.
  - 현재 수정 후의 영화↔영화/영화↔TV/TV↔TV **유형별 수는 아직 측정하지 않음**.
- 최초 FORM 수정 전, 기존에 동일 개념으로 연결될 이웃이 0편이었던 작품: 영화 170 / 드라마 27 = **197편**; 그중 새 연결을 얻은 작품: 영화 155 / 드라마 18 = **173편** (87.8%).
  - 이웃 증가: 영화 평균 237.2 / 중앙값 150 / P99 1,270.49 / 최대 3,002; 드라마 평균 793.4 / 중앙값 686 / P99 2,717.48 / 최대 6,587.
  - **이것도 FORM 수정 전 결과**이므로 재측정 필수.
- `content_pair_baseline_v1` = 동일 개념 공유 작품 쌍. `content_pair_semantic_v1` = 수정 전 일반 의미 관계로 생성한 작품 쌍. `content_pair_new_v1` = 수정 전 신규 연결 작품 쌍. `content_pair_semantic_v1_formfix` = FORM 수정 후 의미 관계 작품 쌍. **기존 테이블은 자동 갱신되지 않는다.**

## 3. 선정 규칙 R1~R5 (이 내용 잃어버리지 말 것)

규칙은 **R1 > R2 > R3 > R4 > R5** 우선순위. 같은 개념 쌍은 가장 높은 선행 규칙에 한 번만 배치.

| 규칙 | 채택 조건 / 해석 | 가중치 | 선정 수 (FORM 필터 전) |
|---|---|---:|---:|
| R1 | Jina 임베딩 근거 **및** KMDb↔TMDb 동일 작품 교차 출처 공동 출현 근거 존재 (`has_jina AND has_cross`) | 0.95 | 2,319 |
| R2 | 교차언어 + Jina 상호 이웃 + 양방향 순위 중 나쁜 순위가 ≤3. R1 제외 | 0.95 | 2,499 |
| R3-A | 교차언어 상호 이웃; 좋은 순위 1~3, 나쁜 순위 4~30 | 0.85 | 4,543 |
| R3-B | 교차언어 상호 이웃; 양방향 순위 모두 4~10 | 0.85 | 4,597 |
| R3-C | 교차언어 상호 이웃; 좋은 순위 ≥4, 나쁜 순위 11~30 | **0.70** | 22,504 |
| R4 | 비교차언어(`is_cross_lingual=false`), 상호 이웃, 양방향 순위 나쁜 값 ≤3, 문자열 중첩·라벨 길이 조건. 앞선 규칙 제외 | 0.85 | 4,087 |
| R5 | Jina 증거는 없고 cross 증거가 있으며 상호 최선 공동출현 및 2개 이상 공동 작품. 앞선 규칙 제외 | 0.60 | 91 |

- R3-A+B = 9,140. R3 전체 = 31,644. 총 선정 = 40,640.
- R4의 `NOT is_cross_lingual`을 '반드시 같은 언어'로 단정하지 않음.
- 후보 중 미선정 관계는 v1 범위 밖일 뿐 오답으로 확정한 것이 아님.
- R3-C 사전 평가: 50쌍 표본 중 맥락 확인 47쌍, 느슨한 RELATED 이상 35/47(74.5%), 엄격 유사 9/47(19.1%). 사전에 정한 기준에 따라 그룹 전체 가중치 0.70. 결과를 보고 C1/C2로 뒤늦게 나누지 않음. 이는 LLM 기반 참고 평가이지 객관적 정답률이 아님. v1에서 같은 표본을 반복 선정하지 않음.
- 기존 R3(4~10) 샘플은 맥락 확인 46쌍 중 느슨한 관련 39/46(84.8%).
- 짧은 한 글자 한글 표현은 무조건 제외하지 않고 `short_term_risk` 플래그만 표시(기존 243개 관계 표시 사례); 맞는 연결도 많기 때문.
- `relation_type`(SAME/BROADER/RELATED 등)을 관계 평가 없이 임의로 채우지 않음. 저장된 가중치·근거와 개념 간 관계 방향은 다름.

## 4. 현재 구조와 사용 범위

### 주요 테이블

- `dictionary_v2.term(term_id, normalized_term, created_at)` — **`term_text` 컬럼 없음**.
- `dictionary_v2.term_concept(term_id, concept_id)` — 표현→개념.
- `dictionary_v2.concept` — 개념.
- `dictionary_v2.content_term(content_id, term_id, source_name, raw_occurrence)` — 작품에 붙은 출처별 표현.
- `dictionary_v2.content_concept_v1(content_id, concept_id, content_type)` — 영화/TV에 대해서만 중복 제거, `(content_id, concept_id)` PK, `(concept_id,content_id)` 인덱스.
- `dictionary_v2.concept_role_v1(concept_id, search_role, role_basis, matched_rule, rule_version, short_term_risk, created_at)`.
- `dictionary_v2.concept_relation_v1(concept_a, concept_b, rule_id, weight, max_similarity, role_a, role_b, role_group, usage_scope, …)` — 40,640개 원래 관계를 보존.
- `dictionary_v2.pair_evidence_jina`, `pair_evidence_cross`, `pair_evidence_wikidata` — 개별 근거. 신규 이력은 삭제하지 말 것.
- `public.content` — `content_id`, `content_type`, `title`, `original_title`, `release_year` 등.

### 역할 및 현재 탐색 범위

- `FORM`: 단편/독립영화, 원작·리메이크, 웹시리즈 등 **형식·제작 형태**. 일반 주제 탐색과 혼합하지 않음.
- `SETTING`: 현재 수치상 58개. 기존 패턴 주로 연도/세기 표현; 역사적인 명명 시대(신라·조선·일제강점기 등)는 아직 TOPIC. **역할 분류와 시대의 근거 연도 정보는 별도 개념**.
- `TOPIC`: 그 외 주제·인물·사건·작품 키워드(현재 기본값 포함).
- FORM↔FORM → `FORMAT`; FORM↔(TOPIC/SETTING) → `EXCLUDED`; FORM이 없는 관계 → `GENERAL`(현재 규칙).
- 역할 변경 시 **`concept_role_v1`만 UPDATE하면 끝이 아님**. `concept_relation_v1.role_a`, `role_b`, `role_group`, `usage_scope` 갱신 후, 저장된 작품 쌍을 다시 계산해야 함.
- `short_term_risk`는 별도 검토 플래그이며 자동 삭제 기준이 아님.

## 5. 실제 작품 경로 검토에서 발견한 문제

### A. 정상이며 유용한 근거

- `라이브공연 ↔ live performance`(라이브 콘서트 영상끼리), `brain surgeon ↔ surgeon`(의료 소재), `social media ↔ social media influencer`, `athlete ↔ 운동선수`, `first love ↔ 짝사랑`, `복수 ↔ revenge`, `조선시대 ↔ joseon dynasty`, `일제강점기 ↔ japanese occupation of korea`.
- 다만 키워드 관련성만으로 작품 줄거리 전체가 유사하다고 보증할 수 없음.

### B. 맞지만 지나치게 넓은 연결 → **삭제보다 랭킹 점수 조정**

- `죽음 ↔ death`, `가족 ↔ family`, `romance ↔ 연애`, `friendship ↔ 친구`, `actor ↔ 배우`, `boys' love (bl) ↔ 사랑`, `romance ↔ 로맨틱코미디` 등.
- 예: `boys' love (bl)`가 쓰인 290편 × `사랑`이 쓰인 356편 = 103,240개 가능한 경로. 다 의미적으로 똑같거나 좋은 추천인 것은 아님.

### C. 형식 키워드 누락 → **이미 수정**

- `단편영화 ↔ short film`: 일반 탐색에서 쓰던 것을 `FORM_FORM / FORMAT`으로 이동 완료.
- 5개 FORM 보완으로 일반 탐색 관계 28개 감소, 신규 작품 쌍 233,554 감소.

### D. 실제 관계 오류 및 시대 충돌 → **미수정**

- `lighthearted ↔ 빛`: '가벼운 분위기' 대 '빛'의 의미 오류 후보. 현재 차단 여부 **미확인**. 개별 차단뿐 아니라 *발음·표면형 유사성으로 생긴 오연결을 탐지하는 위험 플래그* 검토 대상(§10 참고).
- `일제시대 ↔ joseon dynasty (1392–1910)`: R1 0.95, 서로 다른 역사적 시기를 혼동. 최초 경로 381×227=86,487(이 값은 신규 고유 쌍이 아님).
- `고려시대 ↔ joseon dynasty (1392–1910)`: R3 0.70, 다른 시대를 같은 배경처럼 추천하면 안 됨.
- `한국전쟁 ↔ south korea`: '전쟁 사건'과 광범위한 국가 정보; 작품의 소재인지 국가 태그인지 검증해야 함.
- `seoul, south korea ↔ 서울상경`: 지역명과 상경 행위는 다른 개념. 관계의 설명 가능성과 일반 탐색 적합성을 구분.
- R1의 두 근거(Jina 및 공동출현)가 동시에 오판할 수 있음. **두 근거가 겹쳤다고 논리적으로 정확한 관계가 되는 것은 아님.** 실제 왜 같은 작품에 태깅됐는지 원인 가설은 있지만 출처별 작품을 추적하여 검증하기 전까지 확정하지 않음.

### E. 영향 규모가 큰 관계 예시 (FORM 수정 후 GENERAL 상위 경로)

- `boys' love (bl) ↔ 사랑` 103,240; `romance ↔ 연애` 95,120; `복수 ↔ revenge` 89,425; `일제시대 ↔ joseon dynasty` 86,487; `로맨스 ↔ romance` 73,800; `조선시대 ↔ joseon dynasty` 65,149 등.
- 상위 경로 집중도는 **같은 분모·같은 방향 계수**로 계산해야 하며 예전 646만 총합에 단순 비교해 비중이라고 주장하면 안 됨.

## 6. 앞으로의 구현: 순서대로 체크

### STEP 1 — 시대 개념 사전 만들기 [미완료]

목적: 몇 쌍만 수동 차단하지 않고, **시대 개념의 시간적 충돌을 재현 가능한 규칙으로 탐지**.

- `dictionary_v2.era_period_v1`: `era_code` PK, `start_year`, `end_year`, `era_kind`(PERIOD/EVENT/STATE 등), `note`, `source`(근거 출처, 필요 시). 기원전은 음수. 날짜/연도의 관례와 경계 구간 표준화 필요.
- `dictionary_v2.concept_era_v1`: `concept_id`, `era_code`, `mapping_basis`(수동검토/검증된 Wikidata/규칙 등), 검토 상태. 별칭은 `term → term_concept → concept_id`를 통해 연결. 하나의 코드에 여러 한국어/영어 표현 가능.
- 현재 확인한 주요 시대어: 고구려/`kingdom of goguryeo, korea`, 백제/`baekje`, 신라/`silla`/`silla dynasty`/신라시대, 통일신라, 발해, 삼국시대/`three kingdom of korea`, 고려시대/`goryeo dynasty`/고려말·말기, 조선시대/이조시대/`joseon dynasty (1392–1910)`/조선초·중·후기·말기, 대한제국/`korean empire`, 일제강점기/일제시대/일제치하/일제말기/`japanese occupation of korea`, 한국전쟁/`korean war`/`korean war (1950-53)`.
- 추가 확인 필요: `silla` 외 다양한 표기, 한국사 외 `qing dynasty`, `ming dynasty`, `roman empire`, `yuan dynasty`, 시대/연대 표현 등. **현재 grep 결과가 전체 시대어를 완전히 포착했다는 뜻은 아님.**
- `조선족`, `조선일보`, `고려대학교`, `재일조선인`, `소녀시대`, `시대극`, `period drama` 등은 역사 시대 자체와 구별. 단순 부분문자열 매칭으로 시대를 부여하면 안 됨.
- `한국전쟁`은 PERIOD가 아니라 EVENT; 연도가 있다고 해서 모든 사건을 왕조처럼 취급하지 않음. 사건과 시대 간 관계는 별도 검토.
- 정해진 날짜의 정확한 왕조 경계와 용어의 적용 범위(예: 조선/대한제국 겹침, 통일신라/신라 겹침, 조선초/후기 세부 범위)를 주석과 출처로 기록. 임의 수치를 사실처럼 넣지 않음.

**검증:** 개념 ID 매핑 0건/복수 매핑/명백한 오매핑을 조회하고, 예상 주력 시대어가 모두 등록됐는지 확인.

### STEP 2 — 시간 겹침/인접/분리 판정 [미완료]

**자동 계산용 구분(시간 관계; 역사적 동일성/계승을 확정하지 않음):**

1. `OVERLAP`: 두 기간이 실질적으로 겹침 → 동시대 근거 있음, `GENERAL` 유지 후보. 같은 국가·내용을 뜻한다는 보증은 아님.
2. `ADJACENT`: 겹치지 않지만 사이 간격이 사전 결정한 **20년 이하** → 일반 탐색에서 제외하고 별도 `HISTORY`(역사 탐색 *후보*)로 보존 가능. **20년이라는 숫자는 임시 휴리스틱이며 실제 계승 관계를 증명하지 않음**. 검증 후 바꿀 수 있도록 설정값으로 관리.
3. `DISTANT`: 20년 초과 분리 → 일반 탐색에서 제외 (`EXCLUDED` 또는 세부 원인 별도 필드 기록).
4. `UNKNOWN`: 정보 부재/모호/매핑 충돌 → 자동 판정 보류, 기존 사용 범위를 무턱대고 바꾸지 않음.

**중요한 예외:** 연도가 맞닿는 경계(1392, 1910 등)를 양 끝 포함으로 처리하면 `OVERLAP`처럼 오판할 수 있음. 
- 데이터 정책을 명확히 해 **반열린 기간 `[start, end)`** 사용 또는 경계 연도 별도 `BOUNDARY` 처리.
- `일제시대 ↔ 조선시대`는 단순히 1910이 같다고 OVERLAP되어 GENERAL에 남아서는 안 됨. 조선과 대한제국처럼 실제 역사적 중첩이 있는 경우는 명시적으로 설명.
- 중요한 것은 **GENERAL 사용 적합성**이지, 기간이 다르다는 이유로 역사적 연관 관계가 가짜라고 단정하는 일이 아님.
- 기존 `FORMAT`과 FORM 기반 `EXCLUDED`는 시간 규칙이 덮어쓰지 않음.
- 추천 관계 테이블은 40,640개 보존; 가능하면 **판정 결과(temporal_status, reason, rule_version)를 별도 테이블/필드에 기록**하고, 충돌 관계만 `usage_scope` 변경. UPDATE 이전에 후보와 변화 예상 수 미리 검토.

**처음 확인된 서로 다른 시대 그룹 관계 13개(검토 목록):**

- `고구려 ↔ baekje` R1 → 기간 겹침, 유지 후보.
- `대한제국 ↔ joseon dynasty` R1 → 지정 기간에 따른 중첩 여부 및 왕조/국가 용어 의미 검토.
- `일제시대 ↔ joseon dynasty` R1 → 다른 역사 구간; GENERAL 제외 검토 최우선.
- `조선왕조 ↔ korean empire` R1 → 기간/계승 맥락 검토.
- `통일신라 ↔ 신라` R4 → 기간 포함, 유지 후보.
- `baekje ↔ 발해` R3 0.85 → 시기 분리, 일반 탐색 제외 후보.
- `goryeo dynasty ↔ 고구려` R3 0.85 → 멀리 분리, 일반 탐색 제외 후보(실제 고구려 계승 주장은 별도 의미).
- `고려시대 ↔ joseon dynasty` R3 0.70 → 전환기, 일반 탐색 제외·역사 탐색 후보.
- `대한제국 ↔ kingdom of goguryeo, korea` R3 0.70 → 멀리 분리.
- `신라시대 ↔ joseon dynasty` R3 0.70 → 멀리 분리.
- `goryeo dynasty ↔ 신라시대` R3 0.70 → 일부 기간 겹침 여부에 따라 판정.
- `goryeo dynasty ↔ 조선왕조` R3 0.70 → 왕조 경계 전환.
- `three kingdom of korea ↔ 대한제국` R3 0.70 → 멀리 분리.

**`HISTORY` 갱신 전 스키마 점검:** `concept_relation_v1.usage_scope`에 CHECK 제약이나 enum이 있는지 먼저 조회한다. `HISTORY`가 허용되지 않으면 승인된 값 목록과 제약/타입을 먼저 안전하게 확장할 것. 기존 `GENERAL/FORMAT/EXCLUDED` 제약을 무작정 제거하지 말고 원래 정의를 백업·확인.

```sql
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'dictionary_v2.concept_relation_v1'::regclass
  AND contype = 'c';

SELECT column_name, data_type, udt_name
FROM information_schema.columns
WHERE table_schema = 'dictionary_v2'
  AND table_name = 'concept_relation_v1'
  AND column_name = 'usage_scope';
```

**검증:** 위 13개에 대한 새 판정과 `usage_scope`를 표로 확인. `GENERAL → HISTORY/EXCLUDED` 이동 수 및 잘못 남은 사례 점검.

### STEP 3 — 국가·지역 위치 태그 정리 [미완료]

- 후보: `south korea`, `korea`, `japan`, `seoul, south korea`, `qingdao, china`, `..., japan` 등 실제 DB 표현을 추출.
- **국적/제작국/촬영지/서사 배경/주제**를 하나의 'SETTING'으로 동치 취급하지 않음. 출처 필드·키워드 생성 방식(가능하면 원시 메타데이터)을 확인. `seoul, south korea`가 촬영지라고 확정하려면 실제 소스 스키마를 확인할 것.
- `LOCATION` 서브타입 또는 별도 플래그와 역할 근거(`role_basis`)를 저장하는 안 검토.
- 위치↔위치(`서울 ↔ seoul, south korea`)는 남길 수 있으나, 위치↔사건·행위(`south korea ↔ 한국전쟁`, `seoul ↔ 서울상경`)를 **일반 탐색의 강한 유사성 근거로 쓰는지**는 별도 판단.
- 일괄 금지는 정상적인 역사/지리 탐색 관계를 잃을 수 있으므로 영향 수 집계 후 결정.

### STEP 4 — 이전 METADATA 목록 보완 [미완료]

- **찾을 원본 경로(과거 작업 기록 기반, 실제 파일 내용은 작업 재개 시 확인):** `exploration_v3/experiments/keyword_relations/results/07_provisional_entities/07_terms.csv`에서 `term_status = 'METADATA'`인 **91행**. 같은 폴더의 `07_entities.csv`에서는 `entity_type = 'METADATA'`인 행을 확인.
- 과거 확인 사례: `kbs연속극원작`, `mbc연속극원작`, `tv드라마원작`, `short film`, `단편영화`, `독립영화`, `소설원작` 등. 이 목록은 **현재 v2 DB의 FORM 164개와 동일하지 않음**.
- `07_terms.csv`의 91개를 모두 FORM으로 일괄 변경하지 말 것. 실제 목록을 보고 **FORM/역사 시대/위치·국가/기타**를 구분하고, v2 `term.normalized_term` → `term_concept` → `concept_id`로 정규화 매핑 및 중복 확인 후 선택적으로 반영. `SOURCE_V1_METADATA` 같은 출처를 기록할 경우 실제 검토·선정한 항목에만 부여.
- 원본 확인 예: `python -c "import csv; p='exploration_v3/experiments/keyword_relations/results/07_provisional_entities/07_terms.csv'; r=list(csv.DictReader(open(p,encoding='utf-8-sig'))); x=[v for v in r if v.get('term_status')=='METADATA']; print('METADATA',len(x)); print(*x[:10],sep='\n')"`
- 이미 5개 누락(`단편영화`, `독립영화`, `documentary short`, `independent film`, `short film`) 수정 완료. `소설원작`은 이미 FORM이었다. `short`는 이전 조회 결과에서 없었고 영어 일반 형용사일 수 있으므로 무작정 FORM 금지.
- 프로젝트 내 검색에서 `exploration_v3/staging/entity/07_content_metadata.csv`는 **작품 ID/제목/줄거리 44,426줄 파일**이었으며, 키워드 분류 목록이 아니었음. `METADATA_PATTERN` 대량 출력은 위험 플래그/관계 후보일 수 있으므로 91개 원본과 혼동 금지.
- 과거 분류/규칙의 실제 내용과 근거를 확보한 후, `FORM`, 역사 시대, 위치 태그, 기타로 **검토 후 선택적 이동**.

### STEP 5 — 수정 후 작품 쌍 및 효과 전부 재측정 [미완료]

- 새 `usage_scope='GENERAL'` 관계만 이용해 영화/TV 작품 쌍 다시 생성. 기준선(`content_pair_baseline_v1`)은 동일 개념 공유 기준이며 개념 매핑이 변하지 않았다면 재사용 가능.
- 원래 결과 테이블에 덮어쓰지 말고 새로운 버전/스냅샷 이름을 권장. 어떤 수정 후 계산한 값인지 `rule_version`, 측정 날짜와 함께 기록.
- 비교 지표:
  1. 신규 작품 쌍 합계 및 **영화↔영화, 영화↔드라마, 드라마↔드라마** 각각.
  2. 작품별 신규 이웃 분포: 평균, 중앙값, P99, 최대.
  3. 기존 동일 개념 연결이 0인 작품 중 새 이웃이 생긴 수/비율.
  4. 관계별 가능한 경로 수 상위 20 / 상위 1% 집중도(방향 합산 및 중복 계수 정의 통일).
  5. 범용 개념에 의존한 연결의 분포. **'빈출 상위 1% 개념을 뺀 쌍'은 보조 민감도 분석**이지 임의로 '유의미한 참 연결'의 객관적인 수라 부르지 말 것.
- **실행 주의:** 쌍 테이블은 수백만 행; 인덱스·디스크 여유·쿼리 실행계획 확인. 저장된 테이블은 역할/범위 변경에 자동 동기화되지 않음.

### STEP 6 — 탐색 점수 및 품질 검증 [미완료]

- 품질 개선 목적: 흔한 개념 하나로 수백 작품을 똑같이 추천하지 않도록 한다.
- `relationship weight`: R1~R5 근거 기반 점수(현재 값 유지; 신뢰확률로 해석 금지).
- `IDF` 예시: `ln((N+1)/(df(concept)+1))` 형태. N은 영화+TV 대상 작품 20,351(향후 범위가 바뀌면 다시 계산), df는 해당 개념을 가진 작품 수. **개념 A/B 양쪽의 빈도를 어떻게 합치는지 명시**(평균/기하평균/최대 페널티 등 실험).
- `multiple independent paths`: 서로 독립적인 근거 주제일 때 가산; `friendship↔친구`, `friendship↔사랑과우정`처럼 사실상 같은 방향의 중복 증거는 무제한 누적 금지. 합산 상한/다양성 고려.
- 필요하면 작품별 단어 출처 신뢰도, 줄거리와의 관계, 역사/위치 특성도 별도 보조 신호.
- 실제 평가: 규칙별, 빈출/저빈도, 영화↔영화·영화↔드라마·TV↔TV, 다중/단일 경로를 균형 있게 뽑아 **작품 제목 + 두 개념 라벨 + rule_id + weight + 출처 근거** 확인.
- 이전 표본 SQL의 `TABLESAMPLE SYSTEM (5)`는 매 실행마다 달라질 수 있어 `REPEATABLE (20261123)` 등으로 고정. 다만 SYSTEM 방식은 물리적 블록에 편향될 수 있으므로 품질 평가용 **재현 가능한 계층 표본**을 별도로 설계하면 더 좋음.
- '경로를 설명할 수 있음'과 '관계가 정확함'을 분리해서 판정. 사례 몇 개로 전체 정확도 % 주장하지 말 것.
- RAG/LLM은 실제 DB에서 조회한 경로·근거를 자연어로 설명하는 보조 기능으로 사용. 관계 생성 근거를 지어내지 않게 제한.

## 7. 실제 작업 재개 시 가장 먼저 할 일

1. 현재 DB 상태 및 40,640 관계/GENERAL 40,223/FORM 164 확인 — 재생성·리셋 불필요.
2. 시대 코드 후보·경계 연도·양방향 용어 매핑을 **리뷰 가능한 CSV/테이블**로 마련(먼저 SELECT로 검증; 적합한 근거와 미확실 항목 구분).
3. 시대 충돌 관계 13개를 테스트 케이스로 삼아 시간 판정 SQL을 **SELECT로 먼저** 실행. 중요한 `1910` 경계가 OVERLAP으로 오판되지 않는지 확인.
4. `GENERAL`에서 제외할 조건과 `HISTORY`의 용도를 확정, 변경 예상 건수 보고 난 뒤 트랜잭션으로 사용 범위 업데이트.
5. 영향 재측정 → 국가/지역 → 원본 METADATA 확인 → 최종 재측정 → 품질 샘플 및 IDF.

### 빠른 상태 확인 SQL

```sql
-- 역할 분류 합계: FORM 164, SETTING 58, TOPIC 19221 예상
SELECT search_role, COUNT(*)
FROM dictionary_v2.concept_role_v1
GROUP BY search_role ORDER BY search_role;

-- 관계 범위 합계: GENERAL 40223 / FORMAT 103 / EXCLUDED 314 예상
SELECT usage_scope, role_group, COUNT(*)
FROM dictionary_v2.concept_relation_v1
GROUP BY usage_scope, role_group
ORDER BY usage_scope, role_group;

-- 기존 계산 결과는 수정 전 스냅샷이므로 의미를 구분
SELECT COUNT(*) AS before_formfix_new_pairs
FROM dictionary_v2.content_pair_new_v1;

-- FORM 수정 이후의 신규 고유 쌍은 semantic_formfix에서 기준선 제외해 계산
SELECT COUNT(*) AS after_formfix_new_pairs
FROM dictionary_v2.content_pair_semantic_v1_formfix s
WHERE NOT EXISTS (
  SELECT 1
  FROM dictionary_v2.content_pair_baseline_v1 b
  WHERE b.content_a = s.content_a AND b.content_b = s.content_b
);
```

**중요:** `concept_relation_v1`의 역할/범위 UPDATE는 현재 이미 시행됨. 다시 UPDATE할 때에는 기존 FORM 범위를 실수로 GENERAL로 되돌리지 않도록 주의. 관계 유형/시간 판정을 별도 기록하고 변경 전후 카운트를 남길 것.

## 8. 버전 및 결과 보고 기준

- 기준선: `BASELINE` = **동일 개념을 공유하는 영화/드라마 작품 쌍**. 배우·감독·외부 링크 기반의 모든 가능한 연결을 제외한 것은 아님.
- '신규 작품 연결' = `semantic GENERAL`을 통과했지만 BASELINE에는 없는 **서로 다른 content_id의 무순서 고유 쌍**.
- 수치에는 **버전과 시점**을 함께 표기: 예) `v1.0 최초`, `v1.0 FORM-fix`, `v1.0 temporal-fix`, `v1.0 final`.
- 작업 후 변화가 '정말 잘못된 쌍의 감소'인지, '일반 탐색에서 다른 범위로 이동'한 것인지 분리 표현.
- R1의 교차 출처·임베딩 두 근거는 별개 증거이나 오류가 상관될 수 있음. `일제시대↔조선` 사례는 높은 가중치에서도 원천 근거가 같은 방향으로 편향될 수 있음을 보여 줌. 원인 단정 전 관련 콘텐츠를 직접 추적.
- 일반 탐색, 형식 탐색, 향후 역사 탐색은 서로 다른 **사용 목적**이며 같은 관계를 다른 맥락에서 활용하는 것을 막지 않음.

## 9. 남은 논점 / 현재 미확정인 것

- 시기 매핑의 정확한 시작·종료 연도 및 출처; 조선/대한제국처럼 기간 표현이 겹치는 기준.
- 인접 구간 기준 '20년'을 기본값으로 쓸지, 경계(특히 1910)를 어떻게 다룰지.
- `HISTORY`를 실제 UI에서 별도 검색 범위로 제공할지, 내부 후보/상태로만 둘지.
- 국가/위치 키워드의 출처 의미 및 자동 식별 근거.
- 구 v1 METADATA 91개 목록의 **내용 및 v2 매핑 결과**(원본 위치는 STEP 4에 기록; 검증 대기).
- 명백한 개별 오류 (`lighthearted↔빛`) 차단 및 유사 오류 재발 탐지 기준(§10 발음·표면형 위험 플래그 참조).
- IDF 식, 근거 다양성 보정, 점수 상한, 평가 샘플링/품질 기준.

## 10. v1 이후 로드맵 — 현재 미착수 / 제안 단계

**이 섹션은 v1 완료 조건이 아님.** 아래 수치·방법은 과거 논의나 추가 제안이며, 현재 DB에서 확인된 최종 성과/확정 규칙과 구분한다. **v1에서는 추가 LLM 검토를 필수 게이트로 만들지 않는다.**

1. **출처 간 동일 영화 재검색 평가(검증 설계):** KMDb·TMDb 양쪽에 대응하는 영화 **1,432편(과거 논의 수치; 현재 매핑에서 재확인 필요)**을 대상으로, 한 출처의 키워드만 넣고 반대 출처의 동일 영화를 얼마나 잘 찾는지 `Recall@10` 측정. 교차 출처 관계를 만드는 데 사용한 영화가 시험에 재사용되면 정보 누수가 생기므로 **작품 ID 단위** 학습·통계 구축용/평가용 분리 후, 근거 생성 범위까지 재점검. 분할만 하고 기존 전체 데이터로 만든 관계를 그대로 테스트하면 누수가 해결되지 않음. 기존 공통 개념 기준선·희소성 점수와 비교.
2. **줄거리 임베딩 후보(pgvector/Jina, 탐색/연구):** (a) 키워드 관계로 찾은 작품 후보 재정렬, (b) 관계 양 끝 개념을 사용하는 작품들의 줄거리 임베딩 유사성을 집계해 관계의 *보조 증거*로 검토. `OFF` 후보 승격은 검증·임계치·근거 보존 후 별도 버전에서 진행. **줄거리 평균 유사도만으로 의미 관계를 확정하지 않음.** 데이터 유무·장르·출처 편향을 함께 점검.
3. **중복·형태 변형 개념 점검:** `dream/dreams`, `disability/disabled` 등 영어 표제어/활용형 차이를 검토해 별칭·동일성 후보를 관리. **자동 SAME 통합 금지**; 실제 용례와 구분 필요성을 보존.
4. **발음/표면형 오연결 탐지:** `lighthearted↔빛`, `hallyu↔홀`, `catfight↔카투사` 등 *점검 후보*를 출발점으로 로마자 표기/부분 문자열/발음 유사성 플래그를 개발. 한국어 로마자화의 잡음·우연 일치가 많으므로 **자동 삭제 규칙이 아니라 위험 표시 및 검토 우선순위**로 사용. 예시가 실제 현재 v2에 존재하는지는 별도 확인.
5. **합성어 상하위 관계(Kiwi):** `군사독재 → 독재` 같은 형태소 기반 포괄·관련 후보 생성. 표제어 분해만으로 BROADER/NARROWER를 확정하지 않고 의미와 방향성 검증.
6. **Wikidata v1.1:** 과거 논의의 `검색 후보 20,704행`, `RULE_STRONG 0`은 **최신 데이터에서 재집계 필요**. 한국어↔영어 QID 교차 검증, P31(분류 관계) 별도 의미 설계, 표본 검증 후 근거 수준에 따라 후보 승격 검토. `50쌍` 표본과 `90% 이상 RULE_STRONG / 80~90% CONTEXT_SUPPORTED`는 **검토 제안 기준**이지 현재 확정된 품질 결과나 자동 승격 규칙이 아님. 이미 존재하는 `pair_evidence_wikidata` 등을 활용하되 관계 유형·근거·매핑 정확성 확인.
7. **R3-C 추가 검토(선택적, v1 이후):** R3-C가 생성하는 *고유 신규 작품 연결* 영향이 큰 순서로 우선순위를 정하고, 필요시 최대 `100쌍` 소규모 파일럿으로 유용성·비용 검토. **v1에서 완료한 50쌍 표본 평가와 그룹 전체 가중치 0.70은 그대로 유지**. 사후적 C1/C2 분할이나 모든 관계의 2차 LLM 필수 검증으로 바꾸지 않음. 비용 절감보다 구조적 LLM 의존 최소화가 목표.

**향후 구현 기준:** 단계별 파일을 분리하고(개념 매핑 / 근거 추출 / 관계 선택 / 작품 탐색 / 평가), 규칙·버전·근거 및 변경 전후 수치를 남긴다. 실제 결과가 나오기 전에는 성공·정확도·성능 향상을 확정적으로 서술하지 않는다.

---
