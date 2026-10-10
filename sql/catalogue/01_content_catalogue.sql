-- =========================================================
-- 01_content_catalogue.sql
-- Content Catalogue v1
-- =========================================================




-- =========================================================
-- 1. content
-- 작품 공통 정보
-- =========================================================

CREATE TABLE content (
    content_id BIGSERIAL PRIMARY KEY,

    content_type VARCHAR(20) NOT NULL
        CHECK (content_type IN ('movie', 'tv', 'book')),

    title TEXT NOT NULL,
    original_title TEXT,

    -- 영화/TV: 공개 연도
    -- 책: 원작 최초 발행 연도를 알 수 있을 경우 사용
    release_year INTEGER,

    normalized_title TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_content_type_title
    ON content (content_type, normalized_title);

CREATE INDEX idx_content_release_year
    ON content (release_year);


-- =========================================================
-- 2. content_source_map
-- 작품 단위 외부 ID만 관리
-- =========================================================

CREATE TABLE content_source_map (
    content_source_map_id BIGSERIAL PRIMARY KEY,

    content_id BIGINT NOT NULL
        REFERENCES content(content_id)
        ON DELETE CASCADE,

    source_name VARCHAR(30) NOT NULL
        CHECK (
            source_name IN (
                'kmdb_movie',
                'tmdb_movie',
                'tmdb_tv',
                'wikidata'
            )
        ),

    source_id TEXT NOT NULL,
    source_url TEXT,

    match_method VARCHAR(30) NOT NULL
        DEFAULT 'source_import',

    match_confidence NUMERIC(4,3)
        CHECK (
            match_confidence IS NULL
            OR (match_confidence >= 0 AND match_confidence <= 1)
        ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (source_name, source_id)
);

CREATE INDEX idx_content_source_map_content
    ON content_source_map (content_id);


-- =========================================================
-- 3. movie_detail
-- 영화 전용 상세 정보
-- =========================================================

CREATE TABLE movie_detail (
    content_id BIGINT PRIMARY KEY
        REFERENCES content(content_id)
        ON DELETE CASCADE,

    overview TEXT,

    release_date DATE,

    runtime INTEGER
        CHECK (runtime IS NULL OR runtime > 0),

    genres JSONB NOT NULL
        DEFAULT '[]'::jsonb,

    keywords JSONB NOT NULL
        DEFAULT '[]'::jsonb,

    directors JSONB NOT NULL
        DEFAULT '[]'::jsonb,

    cast_members JSONB NOT NULL
        DEFAULT '[]'::jsonb,

    poster_path TEXT,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =========================================================
-- 4. tv_detail
-- TV 프로그램 전용 상세 정보
-- =========================================================

CREATE TABLE tv_detail (
    content_id BIGINT PRIMARY KEY
        REFERENCES content(content_id)
        ON DELETE CASCADE,

    overview TEXT,

    first_air_date DATE,
    last_air_date DATE,

    number_of_seasons INTEGER
        CHECK (
            number_of_seasons IS NULL
            OR number_of_seasons >= 0
        ),

    number_of_episodes INTEGER
        CHECK (
            number_of_episodes IS NULL
            OR number_of_episodes >= 0
        ),

    genres JSONB NOT NULL
        DEFAULT '[]'::jsonb,

    keywords JSONB NOT NULL
        DEFAULT '[]'::jsonb,

    creators JSONB NOT NULL
        DEFAULT '[]'::jsonb,

    cast_members JSONB NOT NULL
        DEFAULT '[]'::jsonb,

    poster_path TEXT,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =========================================================
-- 5. book_edition
-- 도서 판본/서지 정보
--
-- content : book_edition = 1 : N
--
-- =========================================================

CREATE TABLE book_edition (
    book_edition_id BIGSERIAL PRIMARY KEY,

    content_id BIGINT NOT NULL
        REFERENCES content(content_id)
        ON DELETE CASCADE,

    nlk_id TEXT,
    isbn13 VARCHAR(13),
    yes24_product_id TEXT,

    publisher TEXT,
    pub_year INTEGER,

    contributors JSONB NOT NULL DEFAULT '[]'::jsonb,

    is_representative BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (nlk_id),
    UNIQUE (yes24_product_id)
);

CREATE INDEX idx_book_edition_content
    ON book_edition (content_id);

CREATE INDEX idx_book_edition_isbn13
    ON book_edition (isbn13);

-- 하나의 canonical book에는 대표 판본을 최대 하나만 허용
CREATE UNIQUE INDEX uq_book_representative_per_content
    ON book_edition (content_id)
    WHERE is_representative = TRUE;


-- =========================================================
-- RAW DATA
-- 원본 JSON 보존용 테이블
-- =========================================================


-- =========================================================
-- 6. raw_kmdb_movie
-- KMDb 영화 원본
-- =========================================================

CREATE TABLE raw_kmdb_movie (
    raw_kmdb_movie_id BIGSERIAL PRIMARY KEY,

    source_id TEXT,
    source_row_number INTEGER,

    raw_payload JSONB NOT NULL,

    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_raw_kmdb_movie_source
    ON raw_kmdb_movie (source_id);


-- =========================================================
-- 7. raw_tmdb_movie
-- TMDb 영화 원본
-- =========================================================

CREATE TABLE raw_tmdb_movie (
    raw_tmdb_movie_id BIGSERIAL PRIMARY KEY,

    tmdb_id INTEGER NOT NULL,

    raw_payload JSONB NOT NULL,

    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_raw_tmdb_movie_id
    ON raw_tmdb_movie (tmdb_id);


-- =========================================================
-- 8. raw_tmdb_tv
-- TMDb TV 원본
-- =========================================================

CREATE TABLE raw_tmdb_tv (
    raw_tmdb_tv_id BIGSERIAL PRIMARY KEY,

    tmdb_id INTEGER NOT NULL,

    raw_payload JSONB NOT NULL,

    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_raw_tmdb_tv_id
    ON raw_tmdb_tv (tmdb_id);


-- =========================================================
-- 9. raw_nlk_book
-- 국립중앙도서관 국가서지 원본
-- =========================================================

CREATE TABLE raw_nlk_book (
    raw_nlk_book_id BIGSERIAL PRIMARY KEY,

    nlk_id TEXT,

    raw_payload JSONB NOT NULL,

    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_raw_nlk_id
    ON raw_nlk_book (nlk_id);


-- =========================================================
-- 10. raw_yes24_book
-- YES24 도서 원본
-- =========================================================

CREATE TABLE raw_yes24_book (
    raw_yes24_book_id BIGSERIAL PRIMARY KEY,

    yes24_product_id TEXT,

    raw_payload JSONB NOT NULL,

    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_raw_yes24_product_id
    ON raw_yes24_book (yes24_product_id);