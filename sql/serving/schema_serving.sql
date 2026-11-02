-- =====================================================================
-- schema_serving.sql  (GCP 서비스용 / 읽기 전용 스키마)
-- 원본: schema_entity_20261030.sql (로컬 개발 DB, 27개 테이블)
--
-- 이 파일에 포함된 것: 서비스 탐색에 필요한 11개 테이블
--   content, movie_detail, tv_detail, book_edition, content_source_map,
--   content_keyword, entity, entity_alias, entity_relation,
--   content_entity, content_entity_evidence
--
-- 의도적으로 뺀 것
--   - 확장: vector (사용하지 않음)
--   - 테이블: raw_* 5개, content_topic_extraction_* 3개, topic, content_topic,
--             content_entity_candidate* 3개, content_entity_review_queue,
--             content_entity_status_log, entity_type_change_log
--   - 뷰 3개(v_*): 파일럿 content_id와 추출 테이블에 의존
--   - 함수/트리거: set_content_entity_status, guard_content_entity_status
--             (서비스 DB는 읽기 전용이며 로컬에서 만든 결과를 통째로 적재)
--   - FK: content_entity_evidence.extraction_id -> content_topic_extraction_result
--         (컬럼은 유지, 참조 테이블이 없으므로 FK만 제거)
--
-- ID 컬럼은 identity 없이 일반 BIGINT 입니다. 로컬과 같은 값을 COPY로 넣습니다.
-- (content_id, entity_id, content_entity_id 는 로컬과 반드시 동일하게 유지)
--
-- 복원 (한 트랜잭션, 오류 시 전체 취소):
--   psql -v ON_ERROR_STOP=1 -1 -d <DB명> -f schema_serving.sql
--
-- 적재 순서 (FK 순서):
--   1) content
--   2) movie_detail, tv_detail, book_edition, content_source_map
--   3) entity
--   4) content_keyword
--   5) entity_alias
--   6) content_entity
--   7) content_entity_evidence
--   8) entity_relation
--   적재 후: ANALYZE;
-- =====================================================================

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;

-- ---------------------------------------------------------------------
-- 함수: API에서 검색어를 별칭 조회 키와 같은 규칙으로 정규화할 때 사용
-- ---------------------------------------------------------------------
CREATE FUNCTION public.normalize_keyword(x text) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$
    SELECT lower(
        btrim(
            regexp_replace(
                normalize(x, NFC),
                '[[:space:]]+',
                ' ',
                'g'
            )
        )
    );
$$;

-- ---------------------------------------------------------------------
-- content
-- ---------------------------------------------------------------------
CREATE TABLE public.content (
    content_id bigint NOT NULL,
    content_type character varying(20) NOT NULL,
    title text NOT NULL,
    original_title text,
    release_year integer,
    normalized_title text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT content_pkey PRIMARY KEY (content_id),
    CONSTRAINT content_content_type_check CHECK (((content_type)::text = ANY ((ARRAY['movie'::character varying, 'tv'::character varying, 'book'::character varying])::text[])))
);

CREATE INDEX idx_content_release_year ON public.content USING btree (release_year);
CREATE INDEX idx_content_type_title ON public.content USING btree (content_type, normalized_title);

-- ---------------------------------------------------------------------
-- movie_detail / tv_detail / book_edition
-- ---------------------------------------------------------------------
CREATE TABLE public.movie_detail (
    content_id bigint NOT NULL,
    overview text,
    release_date date,
    runtime integer,
    genres jsonb DEFAULT '[]'::jsonb NOT NULL,
    keywords jsonb DEFAULT '[]'::jsonb NOT NULL,
    directors jsonb DEFAULT '[]'::jsonb NOT NULL,
    cast_members jsonb DEFAULT '[]'::jsonb NOT NULL,
    poster_path text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT movie_detail_pkey PRIMARY KEY (content_id),
    CONSTRAINT movie_detail_runtime_check CHECK (((runtime IS NULL) OR (runtime > 0))),
    CONSTRAINT movie_detail_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.content(content_id) ON DELETE CASCADE
);

CREATE TABLE public.tv_detail (
    content_id bigint NOT NULL,
    overview text,
    first_air_date date,
    last_air_date date,
    number_of_seasons integer,
    number_of_episodes integer,
    genres jsonb DEFAULT '[]'::jsonb NOT NULL,
    keywords jsonb DEFAULT '[]'::jsonb NOT NULL,
    creators jsonb DEFAULT '[]'::jsonb NOT NULL,
    cast_members jsonb DEFAULT '[]'::jsonb NOT NULL,
    poster_path text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    crew jsonb DEFAULT '[]'::jsonb NOT NULL,
    networks jsonb DEFAULT '[]'::jsonb NOT NULL,
    production_companies jsonb DEFAULT '[]'::jsonb NOT NULL,
    episode_runtime integer,
    CONSTRAINT tv_detail_pkey PRIMARY KEY (content_id),
    CONSTRAINT tv_detail_number_of_episodes_check CHECK (((number_of_episodes IS NULL) OR (number_of_episodes >= 0))),
    CONSTRAINT tv_detail_number_of_seasons_check CHECK (((number_of_seasons IS NULL) OR (number_of_seasons >= 0))),
    CONSTRAINT tv_detail_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.content(content_id) ON DELETE CASCADE
);

CREATE TABLE public.book_edition (
    book_edition_id bigint NOT NULL,
    content_id bigint NOT NULL,
    isbn13 character varying(13),
    yes24_product_id text,
    publisher text,
    pub_year integer,
    contributors jsonb DEFAULT '[]'::jsonb NOT NULL,
    is_representative boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    description text,
    description_source character varying(50),
    CONSTRAINT book_edition_pkey PRIMARY KEY (book_edition_id),
    CONSTRAINT book_edition_yes24_product_id_key UNIQUE (yes24_product_id),
    CONSTRAINT book_edition_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.content(content_id) ON DELETE CASCADE
);

CREATE INDEX idx_book_edition_content ON public.book_edition USING btree (content_id);
CREATE INDEX idx_book_edition_isbn13 ON public.book_edition USING btree (isbn13);
CREATE UNIQUE INDEX uq_book_edition_isbn13 ON public.book_edition USING btree (isbn13) WHERE (isbn13 IS NOT NULL);
CREATE UNIQUE INDEX uq_book_representative_per_content ON public.book_edition USING btree (content_id) WHERE (is_representative = true);

-- ---------------------------------------------------------------------
-- content_source_map
-- ---------------------------------------------------------------------
CREATE TABLE public.content_source_map (
    content_source_map_id bigint NOT NULL,
    content_id bigint NOT NULL,
    source_name character varying(30) NOT NULL,
    source_id text NOT NULL,
    source_url text,
    match_method character varying(30) DEFAULT 'source_import'::character varying NOT NULL,
    match_confidence numeric(4,3),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT content_source_map_pkey PRIMARY KEY (content_source_map_id),
    CONSTRAINT content_source_map_source_name_source_id_key UNIQUE (source_name, source_id),
    CONSTRAINT content_source_map_match_confidence_check CHECK (((match_confidence IS NULL) OR ((match_confidence >= (0)::numeric) AND (match_confidence <= (1)::numeric)))),
    CONSTRAINT content_source_map_source_name_check CHECK (((source_name)::text = ANY ((ARRAY['kmdb_movie'::character varying, 'tmdb_movie'::character varying, 'tmdb_tv'::character varying, 'wikidata'::character varying, 'data4library_book'::character varying])::text[]))),
    CONSTRAINT content_source_map_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.content(content_id) ON DELETE CASCADE
);

CREATE INDEX idx_content_source_map_content ON public.content_source_map USING btree (content_id);

-- ---------------------------------------------------------------------
-- content_keyword (근거가 참조. 전체 약 12.5만 행이라 전부 적재 권장)
-- ---------------------------------------------------------------------
CREATE TABLE public.content_keyword (
    keyword_id bigint NOT NULL,
    content_id bigint NOT NULL,
    source_name text NOT NULL,
    source_keyword_id text,
    keyword_name text NOT NULL,
    keyword_normalized text NOT NULL,
    source_location jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT content_keyword_pkey PRIMARY KEY (keyword_id),
    CONSTRAINT content_keyword_keyword_name_check CHECK ((length(btrim(keyword_name)) > 0)),
    CONSTRAINT content_keyword_keyword_normalized_check CHECK ((length(btrim(keyword_normalized)) > 0)),
    CONSTRAINT content_keyword_source_name_check CHECK ((source_name = ANY (ARRAY['kmdb'::text, 'tmdb'::text, 'yes24'::text, 'data4library'::text]))),
    CONSTRAINT content_keyword_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.content(content_id)
);

CREATE INDEX idx_content_keyword_content ON public.content_keyword USING btree (content_id);
CREATE INDEX idx_content_keyword_norm ON public.content_keyword USING btree (keyword_normalized, source_name);

-- ---------------------------------------------------------------------
-- entity / entity_alias / entity_relation
-- ---------------------------------------------------------------------
CREATE TABLE public.entity (
    entity_id bigint NOT NULL,
    entity_name text NOT NULL,
    entity_type text NOT NULL,
    wikidata_qid text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT entity_pkey PRIMARY KEY (entity_id),
    CONSTRAINT entity_entity_name_check CHECK ((length(btrim(entity_name)) > 0)),
    CONSTRAINT entity_entity_type_check CHECK ((entity_type = ANY (ARRAY['PERSON'::text, 'EVENT'::text, 'PLACE'::text, 'ORGANIZATION'::text, 'TOPIC'::text, 'KEYWORD'::text, 'TIME_PERIOD'::text, 'METADATA'::text]))),
    CONSTRAINT entity_wikidata_qid_check CHECK (((wikidata_qid IS NULL) OR (wikidata_qid ~ '^Q[1-9][0-9]*$'::text)))
);

CREATE UNIQUE INDEX uq_entity_qid ON public.entity USING btree (wikidata_qid) WHERE (wikidata_qid IS NOT NULL);

CREATE TABLE public.entity_alias (
    alias_id bigint NOT NULL,
    entity_id bigint NOT NULL,
    alias_text text NOT NULL,
    normalized_alias text NOT NULL,
    lang text DEFAULT 'und'::text NOT NULL,
    alias_kind text NOT NULL,
    source text,
    status text DEFAULT 'proposed'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source_ref text,
    CONSTRAINT entity_alias_pkey PRIMARY KEY (alias_id),
    CONSTRAINT entity_alias_entity_id_normalized_alias_lang_key UNIQUE (entity_id, normalized_alias, lang),
    CONSTRAINT entity_alias_alias_kind_check CHECK ((alias_kind = ANY (ARRAY['label'::text, 'synonym'::text, 'translation'::text, 'seed'::text]))),
    CONSTRAINT entity_alias_alias_text_check CHECK ((length(btrim(alias_text)) > 0)),
    CONSTRAINT entity_alias_normalized_alias_check CHECK ((length(btrim(normalized_alias)) > 0)),
    CONSTRAINT entity_alias_status_check CHECK ((status = ANY (ARRAY['proposed'::text, 'verified'::text, 'rejected'::text]))),
    CONSTRAINT entity_alias_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entity(entity_id)
);

CREATE INDEX idx_entity_alias_lookup ON public.entity_alias USING btree (normalized_alias, lang);

CREATE TABLE public.entity_relation (
    entity_relation_id bigint NOT NULL,
    from_entity_id bigint NOT NULL,
    to_entity_id bigint NOT NULL,
    relation_type text NOT NULL,
    fact_status text DEFAULT 'proposed'::text NOT NULL,
    evidence_status text DEFAULT 'unchecked'::text NOT NULL,
    relation_basis text NOT NULL,
    next_hop_eligible boolean DEFAULT false NOT NULL,
    policy_version text,
    source_ref text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT entity_relation_pkey PRIMARY KEY (entity_relation_id),
    CONSTRAINT entity_relation_from_entity_id_to_entity_id_relation_typ_key UNIQUE (from_entity_id, to_entity_id, relation_type),
    CONSTRAINT chk_entity_relation_eligible_verified CHECK (((next_hop_eligible = false) OR ((fact_status = 'verified'::text) AND (evidence_status = 'checked'::text)))),
    CONSTRAINT entity_relation_check CHECK ((from_entity_id <> to_entity_id)),
    CONSTRAINT entity_relation_check1 CHECK (((relation_type <> 'RELATED'::text) OR (from_entity_id < to_entity_id))),
    CONSTRAINT entity_relation_evidence_status_check CHECK ((evidence_status = ANY (ARRAY['unchecked'::text, 'checked'::text, 'insufficient'::text]))),
    CONSTRAINT entity_relation_fact_status_check CHECK ((fact_status = ANY (ARRAY['proposed'::text, 'verified'::text, 'rejected'::text]))),
    CONSTRAINT entity_relation_relation_basis_check CHECK ((relation_basis = ANY (ARRAY['source_fact'::text, 'semantic_inference'::text]))),
    CONSTRAINT entity_relation_relation_type_check CHECK ((relation_type = ANY (ARRAY['BROADER'::text, 'RELATED'::text, 'TEMPORAL_WITHIN'::text, 'LOCATED_IN'::text]))),
    CONSTRAINT entity_relation_from_entity_id_fkey FOREIGN KEY (from_entity_id) REFERENCES public.entity(entity_id),
    CONSTRAINT entity_relation_to_entity_id_fkey FOREIGN KEY (to_entity_id) REFERENCES public.entity(entity_id)
);

CREATE INDEX idx_entity_relation_target ON public.entity_relation USING btree (to_entity_id);
CREATE INDEX idx_entity_relation_eligible ON public.entity_relation USING btree (from_entity_id, to_entity_id) WHERE next_hop_eligible;

-- ---------------------------------------------------------------------
-- content_entity (작품-개념 연결)
-- ---------------------------------------------------------------------
CREATE TABLE public.content_entity (
    content_entity_id bigint NOT NULL,
    content_id bigint NOT NULL,
    entity_id bigint NOT NULL,
    link_role text NOT NULL,
    fact_status text DEFAULT 'proposed'::text NOT NULL,
    evidence_status text DEFAULT 'unchecked'::text NOT NULL,
    relation_basis text NOT NULL,
    next_hop_eligible boolean DEFAULT false NOT NULL,
    policy_version text,
    score numeric(12,6),
    score_version text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT content_entity_pkey PRIMARY KEY (content_entity_id),
    CONSTRAINT content_entity_content_id_entity_id_link_role_key UNIQUE (content_id, entity_id, link_role),
    CONSTRAINT chk_content_entity_eligible_verified CHECK (((next_hop_eligible = false) OR ((fact_status = 'verified'::text) AND (evidence_status = 'checked'::text)))),
    CONSTRAINT content_entity_evidence_status_check CHECK ((evidence_status = ANY (ARRAY['unchecked'::text, 'checked'::text, 'insufficient'::text]))),
    CONSTRAINT content_entity_fact_status_check CHECK ((fact_status = ANY (ARRAY['proposed'::text, 'verified'::text, 'rejected'::text]))),
    CONSTRAINT content_entity_link_role_check CHECK ((link_role = ANY (ARRAY['subject'::text, 'depicts'::text, 'setting'::text, 'director'::text, 'cast'::text, 'based_on'::text]))),
    CONSTRAINT content_entity_relation_basis_check CHECK ((relation_basis = ANY (ARRAY['source_fact'::text, 'semantic_inference'::text]))),
    CONSTRAINT content_entity_score_check CHECK (((score IS NULL) OR (score >= (0)::numeric))),
    CONSTRAINT content_entity_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.content(content_id),
    CONSTRAINT content_entity_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entity(entity_id)
);

CREATE INDEX idx_content_entity_content ON public.content_entity USING btree (content_id);
CREATE INDEX idx_content_entity_entity ON public.content_entity USING btree (entity_id);
-- 탐색 쿼리용: 개념 -> 작품 이동 (허용된 연결만)
CREATE INDEX idx_content_entity_eligible ON public.content_entity USING btree (entity_id, content_id) WHERE next_hop_eligible;

-- ---------------------------------------------------------------------
-- content_entity_evidence (연결 근거)
-- extraction_id 컬럼은 유지하되, 추출 이력 테이블이 없으므로 FK는 두지 않음
-- ---------------------------------------------------------------------
CREATE TABLE public.content_entity_evidence (
    evidence_id bigint NOT NULL,
    content_entity_id bigint NOT NULL,
    evidence_type text NOT NULL,
    source_name text,
    source_ref text,
    source_group text,
    keyword_id bigint,
    extraction_id bigint,
    evidence_text text,
    evidence_location jsonb,
    quote_valid boolean,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT content_entity_evidence_pkey PRIMARY KEY (evidence_id),
    CONSTRAINT content_entity_evidence_check CHECK (((evidence_type <> 'db_keyword'::text) OR (keyword_id IS NOT NULL))),
    CONSTRAINT content_entity_evidence_check1 CHECK (((evidence_type <> 'llm_topic'::text) OR (extraction_id IS NOT NULL))),
    CONSTRAINT content_entity_evidence_check2 CHECK (((keyword_id IS NULL) OR (evidence_type = 'db_keyword'::text))),
    CONSTRAINT content_entity_evidence_check3 CHECK (((extraction_id IS NULL) OR (evidence_type = 'llm_topic'::text))),
    CONSTRAINT content_entity_evidence_evidence_type_check CHECK ((evidence_type = ANY (ARRAY['db_keyword'::text, 'llm_topic'::text, 'wikipedia'::text, 'wikidata'::text, 'metadata'::text]))),
    CONSTRAINT content_entity_evidence_content_entity_id_fkey FOREIGN KEY (content_entity_id) REFERENCES public.content_entity(content_entity_id),
    CONSTRAINT content_entity_evidence_keyword_id_fkey FOREIGN KEY (keyword_id) REFERENCES public.content_keyword(keyword_id)
);

CREATE INDEX idx_entity_evidence_link ON public.content_entity_evidence USING btree (content_entity_id);
CREATE UNIQUE INDEX uq_evidence_keyword ON public.content_entity_evidence USING btree (content_entity_id, keyword_id) WHERE (keyword_id IS NOT NULL);
CREATE UNIQUE INDEX uq_evidence_extraction ON public.content_entity_evidence USING btree (content_entity_id, extraction_id) WHERE (extraction_id IS NOT NULL);

-- ---------------------------------------------------------------------
-- (선택) 제목 부분 검색용. 필요할 때 주석을 풀어 사용
-- ---------------------------------------------------------------------
-- CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- CREATE INDEX idx_content_title_trgm ON public.content USING gin (title gin_trgm_ops);

-- ---------------------------------------------------------------------
-- 함수 실행 권한: 기본값은 PUBLIC 실행 가능이므로 명시적으로 회수한다.
-- (소유자/슈퍼유저는 영향 없음. 앱 역할에는 아래 GRANT로 별도 부여)
-- ---------------------------------------------------------------------
REVOKE ALL ON FUNCTION public.normalize_keyword(text) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- (선택) 읽기 전용 앱 역할. 비밀번호를 정한 뒤 실행
-- ---------------------------------------------------------------------
-- CREATE ROLE capstone_app LOGIN PASSWORD '<여기에 비밀번호>';
-- GRANT USAGE ON SCHEMA public TO capstone_app;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO capstone_app;
-- GRANT EXECUTE ON FUNCTION public.normalize_keyword(text) TO capstone_app;
