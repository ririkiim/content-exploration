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
- content_topic
- content_id PK/FK → content.content_id
- topic_id PK/FK → topic.topic_id
- INDEX(topic_id)

