# 코드 AUTO 규칙 실험 v1.2 (실험 산출물 — v1 정책·원장·DB 미변경)

## 파이프라인 구조
`코드 AUTO_SAME → (불확실한 후보만) LLM → 나머지 HOLD`
- 코드 AUTO: 기존 규칙 53쌍(normalised_equal 45 + unit_numeral 8, 불변) + 신규 규칙 N1/N2/N3/N5. 병합 후보로 자동 반영(DB 반영 정책은 별도 결정).
- LLM 대상: 코드 관계 증거는 있으나 AUTO 조건 미달(ASSISTED_SAME, N4 강등분 포함)인 후보 + 기존 v0.3 승격 후보 중 AUTO가 아닌 것. 원장에 이미 판정이 있으면 재호출하지 않음(pair_key 재사용).
- HOLD: 자동 병합하지 않는 상태. 사람이 반드시 봐야 하는 대기열이 아님.
- 거부권(veto): 숫자 충돌, 기존 엔티티 충돌, 공유 Wikidata 항목이 인물/밴드/작품/동음이의 type뿐이거나 깨끗한 공유 항목이 2개 이상.

## 신규 AUTO 규칙 (v1.2)
| 규칙 | 근거(관계 + 사용) | 범위 |
|---|---|---|
| N1 qid+translit | 한영 라벨이 같은 깨끗한 Wikidata 항목 + 한글 자음골격 = 영어 자음골격 | 한영 외래어 |
| N2 qid+compose | 위 QID + 단어별 조합 일치 + 공출현≥1 | 한영 복합어 |
| N3 qid+cooc | 위 QID + 같은 콘텐츠에 두 키워드 동시 부착 | 한영 일반 |
| N5 spell+substitution+QID | 한 음절 한 성분 치환(철자 혼동군) + 두 철자가 공출현하지 않음 + 두 철자가 한 Wikidata 항목의 라벨/별칭 | 같은 언어 한국어 |
| (강등) N4 translit+cooc | 증거는 있으나 오류 유형 2종(false friend, 모음 소실) → ASSISTED | |

단일 신호(QID만, translit만, 공출현만, E5)는 어떤 경우에도 AUTO가 되지 않는다.

## 오류 분석 → 규칙 수정 (개별 쌍 수동 승인 없음)
E1 ㅡ↔ㅣ 혼동(섹스/섹시) 제거 · E2 3글자 골격 충돌(엄마와아들/mother) 차단 · E3 파생형(마라토너/marathon) 차단 · E4 false friend/모음 소실(펜션/pension, 타임리프/time loop) → N4 강등 · E5 철자 규칙이 전체 후보에서 폭주(172건, 대부분 다른 단어: 납치/넙치, 모녀/마녀) → N5는 QID 증거 필수(영어 골격 삼각검증도 시도했으나 실패) · E6/E7 규칙으로 잡을 수 없는 항목(여고생/high school, 성형외과) 기록.
E1~E3·E5는 관찰된 사례에서 만든 사후 규칙이므로 독립 데이터에서 재검증되기 전까지 "실험" 상태.

## 재현·회귀 테스트
`python3 exp_report.py` (네트워크·LLM 불필요, 약 20초). 종료 코드 1이면 hard 테스트 실패.
hard: 기존 53 유지 / CODE_NOT_SAME 승격 0 / AUTO 중 LLM·사람 NOT_SAME 0 / 단일신호 AUTO 0 / 알려진 translit 오탐 3종 차단. soft: AUTO 중 UNSURE 목록.
LLM·사람 판정은 충돌 탐지에만 쓰며 정답으로 가정하지 않는다.

## 산출물
rules_summary.json · regression_report.json(v1.0/1.1/1.2) · rule_revision.json · population_auto_pairs.csv(전체 308,625쌍 재적용) · validation_sample.csv(1회성 26쌍) · pipeline_metrics.json
