## Content Catalogue v1

### 데이터 출처

| 콘텐츠 | 기준 데이터 | 보강 데이터 |
|---|---|---|
| 영화 | KMDb | TMDb |
| 드라마 | TMDb | - |
| 책 | 국가서지 | YES24 |

- 영화: KMDb를 전체 후보 기준으로 사용하고 TMDb 상세정보 보강
- 드라마: TMDb를 전체 후보 및 상세정보 기준으로 사용
- 책: 국가서지를 전체 후보로 사용하고 필요한 도서는 YES24로 상세정보 보강

#### 국가서지
- 출처: 국립중앙도서관 국가서지 LOD
- 데이터: 일반도서 데이터셋 (JSON-LD)
- [데이터 다운로드](https://lod.nl.go.kr/home/dataset/datadownload.do#data-bibliographic)
- 원본은 raw 테이블에 보존하고 서비스용 데이터만 별도로 정제하여 사용

---

## 전체 파이프라인

### 1. 콘텐츠 카탈로그 구축

```text
KMDb 영화 ───────┐
TMDb 영화 ───────┤
TMDb 드라마 ─────┼──→ Raw 데이터 보존
국가서지 도서  ────┤
YES24 도서 ──────┘
                       ↓
              정제 / 정규화 / 중복 처리
                       ↓
                Content Catalogue
                       │
        ┌──────────────┴──────────────┐
        ↓                             ↓
     content                 content_source_map
(canonical content)        (외부 ID ↔ content_id)
        │
   ┌────┴────┐
   ↓         ↓
movie_tv_  book_edition
 detail
```

### 2. 관계 탐색 파이프라인
```text
사용자 콘텐츠 검색
        ↓
content_id 식별
        ↓
구조화 데이터 관계 추출
        ↓
Wikidata / Wikipedia 보강
        ↓
Entity / 관계 후보 생성
        ↓
LLM 의미 확장 및 후보 정리
        ↓
관련 Content 후보 검색
        ↓
관계 / 맥락 검증 및 점수화
        ↓
Relationship 저장
        ↓
탐색 경로 생성 / 추천
```

### 전체 흐름 요약
```text
외부 데이터 수집
→ Raw 보존
→ Content Catalogue 구축
→ 콘텐츠 검색
→ Entity / 관계 후보 추출
→ Wikidata·Wikipedia 보강
→ LLM 의미 확장·후보 정리
→ 관련 콘텐츠 탐색
→ 관계 / 맥락 검증 및 점수화
→ Relationship 구축
→ 탐색 경로 추천
```
---

### 테이블 구조

```text
Database
│
├─ Raw Data
│  ├─ raw_kmdb_movie
│  ├─ raw_tmdb_movie
│  ├─ raw_tmdb_tv
│  ├─ raw_nlk_book
│  └─ raw_yes24_book
│
├─ Content Catalogue
│  ├─ content
│  │  └─ 작품 단위 canonical identity
│  │
│  ├─ movie_tv_detail
│  │  └─ 영화/드라마 상세정보 (content와 1:1)
│  │
│  ├─ book_edition
│  │  └─ 도서 판본/서지정보 (content와 1:N)
│  │
│  └─ content_source_map
│     └─ 작품 단위 외부 source ID ↔ content_id
│
└─ Relationship Data
   ├─ entity
   │  └─ 인물/사건/시대 등 탐색 대상 entity
   │
   └─ relationship
      └─ content/entity 간 관계 및 근거 저장
```

---
### 주요 컬럼
#### content

- content_id (PK)
- content_type
- title
- original_title
- release_year
- normalized_title

#### movie_tv_detail

영화/드라마 상세정보 (content와 1:1)

- content_id (PK, FK)
- overview
- genres
- keywords
- creators
- cast_members
- poster_path

#### book_edition

도서의 판본/서지정보 (content와 1:N)

- book_edition_id (PK)
- content_id (FK)
- nlk_id
- isbn_raw
- isbn13
- yes24_product_id
- raw_title
- normalized_title
- publisher
- pub_year
- contributors
- kdc
- introduction
- toc
- volume_no
- series_key
- work_key
- is_representative

#### content_source_map

작품 단위 외부 ID와 canonical content 연결

- content_source_map_id (PK)
- content_id (FK)
- source_name
- source_id
- source_url
- match_method
- match_confidence

#### raw data
 
원천 데이터는 별도 raw 테이블에 원본 형태로 보존

> 세부 컬럼 구성은 프로토타입 구현 과정에서 조정 가능

### 추후 논의 필요

- **Topic 구조 및 저장 방식**
  - LLM에서 추출한 작품의 주제·갈등·분위기 등의 의미 정보를 어떻게 저장하고 재사용할지
  - Entity와 별도로 관리할지, 관계를 어떤 방식으로 연결할지

- **Entity 외부 ID 관리 방식**
  - Wikidata 외 추가적인 외부 식별자를 관리하기 위해 `entity_source_map`을 별도로 둘지는 추후 논의

- **검색용 데이터 구성 방식 (`search_document / index`)**
  - 영화·드라마·책의 검색에 필요한 정보가 여러 테이블에 나뉘어 있기 때문에, 이를 `content_id` 단위로 모아 검색하기 쉽게 만든 파생 데이터
  - 현재는 `content`의 공통 필드를 바로 검색에 사용할지, 추후 별도의 검색용 계층을 만들지 논의 필요