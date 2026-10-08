# content-exploration
Evidence-grounded cross-media exploration across movies, TV series, and books.

# 테이블 구조 (임시)

### content
content_id PK
content_type
title
release_date
description
normalized_title


### movie
content_id PK/FK
genre
nation
keywords


### tv
content_id PK/FK
genre
nation
keywords


### book
content_id PK/FK
goods_sort_nm
isbn10
isbn13


### content_source_map
content_id FK
source_name
source_id
match_method

UNIQUE(source_name, source_id)
