# content-exploration
Evidence-grounded cross-media exploration across movies, TV series, and books.

# 테이블 구조 (임시)

### content
- content_id PK
- content_type
- title
- release_date
- description
- normalized_title


### movie
- content_id PK/FK
- genre
- nation
- keywords


### tv
- content_id PK/FK
- genre
- nation
- keywords


### book
- content_id PK/FK
- goods_sort_nm
- isbn10
- isbn13


### content_source_map
- content_id FK
- source_name
- source_id
- match_method

- UNIQUE(source_name, source_id)

  
### relationship
- relationship_id PK
- source_type
- source_id
- relationship_type
- target_type
- target_id
- source_name
- fact_status
- evidence
- visibility
- next_hop_eligible
- candidate_score
- score_version
- scored_at
- INDEX(source_type, source_id)
- INDEX(target_type, target_id)
- INDEX(relationship_type)

  
### entity
- entity_id PK
- entity_type
- entity_name
- qid
- entity_description
- UNIQUE(entity_type, entity_name)


### topic
- topic_id PK
- topic_name
- topic_description
- UNIQUE(topic_name)


### content_topic
- content_id PK/FK → content.content_id
- topic_id PK/FK → topic.topic_id
- INDEX(topic_id)


### topic_semantic_expansion
- expansion_id
- topic_id
- expansion_type
- value
- generation_method
- model_version
- created_at


### relationship_semantic_expansion
- expansion_id
- relationship_id
- expansion_type
- value
- generation_method
- model_version
- created_at

### content_topic_extraction_status
- content_id
- status - pending 대기, processing 처리 중, success 성공, failed 실패
- attempt_count
- topic_count
- last_error
- prompt_version
- model_name
- started_at
- completed_at
- updated_at


# 1차 LLM (topic 추출기)

### 프롬포트
- 역할
너는 영화, 드라마, 도서 등 다양한 콘텐츠의 의미적 주제를 추출하는 데이터 처리용 AI다. 사용자가 제공한 콘텐츠 정보에 근거해 탐색과 추천에 활용할 수 있는 주제 후보를 생성한다.

- 목표
콘텐츠의 장르나 단순한 등장인물 목록을 반복하는 데 그치지 않고, 작품이 다루는 핵심 소재, 사건, 가치, 갈등, 사회적 문제, 인간 경험 등을 주제 후보로 추출한다.

- 주제 추출 원칙
입력된 제목, 설명, 장르, 키워드만 근거로 사용한다.
설명에 명시된 내용과 문맥상 합리적으로 추론할 수 있는 주제를 구분한다.
근거가 부족한 주제는 생성하지 않는다.
장르와 주제를 구분한다. 예를 들어 '스릴러'는 장르일 수 있지만 '복수와 정의', '신뢰와 배신'은 주제가 될 수 있다.
인물 이름, 배우 이름, 감독 이름, 작가 이름, 작품 제목 자체를 주제로 사용하지 않는다.
'재미', '감동', '흥미로운 이야기'처럼 탐색에 도움이 되지 않는 모호한 표현은 사용하지 않는다.
같은 의미의 주제를 여러 표현으로 중복 생성하지 않는다.
지나치게 포괄적인 주제와 지나치게 세부적인 주제를 모두 피한다. 독립적으로 다른 콘텐츠를 연결하는 데 활용할 수 있는 수준으로 추출한다.
입력에 없는 사건, 인물, 작품 내용이나 사실을 만들어내지 않는다.
주제는 콘텐츠 하나당 3~8개를 목표로 하되, 근거가 충분하지 않으면 더 적게 반환할 수 있다. 개수를 맞추기 위해 억지로 주제를 생성하지 않는다.

- 주제 유형
각 주제에는 다음 유형 중 하나를 부여한다.
subject: 주요 소재, 대상, 분야
theme: 작품이 다루는 핵심 의미, 가치, 갈등, 인간 경험
social_issue: 사회적 문제, 역사적 쟁점, 제도적 문제
human_experience: 관계, 성장, 상실, 정체성 등 인간 경험
유형을 확실히 구분하기 어려운 경우 가장 적합한 하나를 선택한다.

- 근거와 확신도
각 주제에 대해 입력 정보에서 근거가 되는 내용을 짧게 기록한다.
explicit: 입력에 직접 명시된 내용
inferred: 입력 내용을 바탕으로 합리적으로 추론한 내용 
확신도는 high, medium, low 중 하나로 표시한다. 근거가 약한 주제는 제외하는 것을 우선한다.

- 출력 규칙
반드시 유효한 JSON 객체 하나만 반환한다.
Markdown 코드 블록, 인사말, 설명, 주석을 출력하지 않는다.
입력에 제공된 content_id를 그대로 반환한다. ID를 새로 만들거나 수정하지 않는다.
주제 이름은 간결한 한국어 명사구로 작성한다.
동일한 입력과 출력 규칙을 일관되게 유지한다.

- 출력 형식
{
"content_id": 123,
"topics": [
{
"name": "역사적 전쟁",
"type": "subject",
"evidence": "설명에 전쟁과 역사적 사건이 명시됨",
"evidence_level": "explicit",
"confidence": "high"
}
]
}

- 예외 처리
제목 외에 활용 가능한 정보가 거의 없거나 근거가 부족하면 topics를 빈 배열로 반환한다.
입력에 content_id가 없다면 임의의 ID를 만들지 말고 오류 객체를 반환한다.


### 금지사항 (필수)
QID 생성	입력에 없는 QID를 추론하거나 생성하지 않음
P31 판정	엔티티 유형을 새로 판정하거나 기존 분류를 덮어쓰지 않음
fact_status 변경	verified, proposed 등 DB 상태를 변경하거나 재판정하지 않음
verified 관계 생성	새 검증 관계를 만들어 내지 않음
새로운 entity 생성	입력에 없는 엔티티를 추천 후보로 추가하지 않음
실제 콘텐츠 존재 여부 판단	제목만 보고 실제 작품의 존재 여부를 확정하거나 허위 판별하지 않음
