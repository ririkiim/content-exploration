# 키워드 관계 기반 작품 경로 탐색 프로토타입 (오프라인)
DB·원장·정책 미변경. 신규 LLM/Wikidata 호출 없음. 입력: `../edges.csv`(임시 관계 사전) + `impact_*/input`의 content·content_keyword CSV.

## 사용법 (약 15초/실행)
    python3 explore.py --content "올드보이" --max-hops 1 --top 15
    python3 explore.py --content "변호인" --content "쉬리" --max-hops 2 --only-hops 2 --top 6 --json out.json
    python3 explore.py --stats --sample 300          # 시작 작품 표본 300개의 결과 유무·구성
옵션: `--pick N`(제목 중복 시 선택) · `--content-id` · `--include-shared`(이미 키워드를 공유하는 작품 포함) · `--include-candidates`(E5 단독 후보 층 포함, 기본 제외) · 3홉은 미지원(보류).

## 결과 항목
작품명·연도·유형·출처 / 시작 키워드(원문)·끝 키워드(원문)와 각 키워드의 출처(kmdb/tmdb) / 관계 유형(EXACT·CLOSE·RELATED·BROADER)·edge_type·가중치·근거·출처(provenance) / 홉 수 / 경로 점수 / 같은 작품으로 가는 다른 경로 수 / 한·영 출처 교차 여부.
EXACT는 병합이 아니라 엣지로 반환하며 원본 키워드를 보존한다.

## 점수(미튜닝)
`score = Π(엣지 가중치) × 0.7^(홉−1) × 1/(1+log10(경로상 최대 키워드 df))`. 작품별 최고 점수 경로 하나만 대표로 반환.
구조 가드(점수 아님): BROADER+BROADER 2홉은 상향 체인(좁은→넓은→더 넓은)만 허용. 추상 부모를 거치는 형제 경로(protest→conflict→해전)는 제외.

## 한계
키워드가 없는 작품(53%)은 시작·도착 모두 불가. 관계 사전은 임시이며 Wikidata 캐시(용어의 약 5%)·LLM 판정(llm_only)을 포함. 3홉 미지원. 점수 가중치는 검증된 값이 아님.
