
BEGIN;

-- 1. 원본 키워드
CREATE TABLE public.content_keyword (
    keyword_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    content_id BIGINT NOT NULL REFERENCES public.content(content_id),
    source_name TEXT NOT NULL
        CHECK (source_name IN ('kmdb','tmdb','yes24','data4library')),
    source_keyword_id TEXT,
    keyword_name TEXT NOT NULL,
    keyword_normalized TEXT NOT NULL,
    source_location JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (length(btrim(keyword_name)) > 0),
    CHECK (length(btrim(keyword_normalized)) > 0)
);

CREATE UNIQUE INDEX uq_content_keyword
ON public.content_keyword (
    content_id,
    source_name,
    COALESCE(source_keyword_id, ''),
    keyword_normalized
);

CREATE INDEX idx_content_keyword_norm
ON public.content_keyword(keyword_normalized, source_name);

CREATE INDEX idx_content_keyword_srcid
ON public.content_keyword(source_name, source_keyword_id)
WHERE source_keyword_id IS NOT NULL;


-- 2. 정규화된 공통 개념
CREATE TABLE public.concept (
    concept_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    concept_name TEXT NOT NULL,
    concept_type TEXT NOT NULL CHECK (
        concept_type IN (
            'PERSON','EVENT','PLACE','ORGANIZATION',
            'TOPIC','KEYWORD','TIME_PERIOD','METADATA'
        )
    ),
    wikidata_qid TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (length(btrim(concept_name)) > 0),
    CHECK (
        wikidata_qid IS NULL
        OR wikidata_qid ~ '^Q[1-9][0-9]*$'
    )
);

CREATE UNIQUE INDEX uq_concept_qid
ON public.concept(wikidata_qid)
WHERE wikidata_qid IS NOT NULL;


-- 3. 개념 별칭
CREATE TABLE public.concept_alias (
    alias_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    concept_id BIGINT NOT NULL REFERENCES public.concept(concept_id),
    alias_text TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'und',
    alias_kind TEXT NOT NULL CHECK (
        alias_kind IN ('label','synonym','translation','seed')
    ),
    source TEXT,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','verified','rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (concept_id, normalized_alias, lang),
    CHECK (length(btrim(alias_text)) > 0),
    CHECK (length(btrim(normalized_alias)) > 0)
);

CREATE INDEX idx_concept_alias_lookup
ON public.concept_alias(normalized_alias, lang);


-- 4. 콘텐츠-개념 연결
CREATE TABLE public.content_concept (
    content_concept_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    content_id BIGINT NOT NULL REFERENCES public.content(content_id),
    concept_id BIGINT NOT NULL REFERENCES public.concept(concept_id),
    link_role TEXT NOT NULL CHECK (
        link_role IN (
            'subject','depicts','setting',
            'director','cast','based_on'
        )
    ),
    fact_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (fact_status IN ('proposed','verified','rejected')),
    evidence_status TEXT NOT NULL DEFAULT 'unchecked'
        CHECK (evidence_status IN ('unchecked','checked','insufficient')),
    relation_basis TEXT NOT NULL CHECK (
        relation_basis IN ('source_fact','semantic_inference')
    ),
    next_hop_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    policy_version TEXT,
    score NUMERIC(12,6),
    score_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (content_id, concept_id, link_role),
    CHECK (score IS NULL OR score >= 0)
);

CREATE INDEX idx_content_concept_concept
ON public.content_concept(concept_id);

CREATE INDEX idx_content_concept_content
ON public.content_concept(content_id);


-- 5. 연결 근거
CREATE TABLE public.content_concept_evidence (
    evidence_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    content_concept_id BIGINT NOT NULL
        REFERENCES public.content_concept(content_concept_id),
    evidence_type TEXT NOT NULL CHECK (
        evidence_type IN (
            'db_keyword','llm_topic','wikipedia',
            'wikidata','metadata'
        )
    ),
    source_name TEXT,
    source_ref TEXT,
    source_group TEXT,
    keyword_id BIGINT REFERENCES public.content_keyword(keyword_id),
    extraction_id BIGINT
        REFERENCES public.content_topic_extraction_result(extraction_id),
    evidence_text TEXT,
    evidence_location JSONB,
    quote_valid BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (evidence_type <> 'db_keyword' OR keyword_id IS NOT NULL),
    CHECK (evidence_type <> 'llm_topic' OR extraction_id IS NOT NULL),
    CHECK (keyword_id IS NULL OR evidence_type = 'db_keyword'),
    CHECK (extraction_id IS NULL OR evidence_type = 'llm_topic')
);

CREATE UNIQUE INDEX uq_evidence_keyword
ON public.content_concept_evidence(content_concept_id, keyword_id)
WHERE keyword_id IS NOT NULL;

CREATE UNIQUE INDEX uq_evidence_extraction
ON public.content_concept_evidence(content_concept_id, extraction_id)
WHERE extraction_id IS NOT NULL;

CREATE INDEX idx_concept_evidence_link
ON public.content_concept_evidence(content_concept_id);

CREATE INDEX idx_concept_evidence_source_group
ON public.content_concept_evidence(source_group);


-- 6. 개념 간 관계
CREATE TABLE public.concept_relation (
    concept_relation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_concept_id BIGINT NOT NULL REFERENCES public.concept(concept_id),
    to_concept_id BIGINT NOT NULL REFERENCES public.concept(concept_id),
    relation_type TEXT NOT NULL CHECK (
        relation_type IN (
            'BROADER','RELATED','TEMPORAL_WITHIN','LOCATED_IN'
        )
    ),
    fact_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (fact_status IN ('proposed','verified','rejected')),
    evidence_status TEXT NOT NULL DEFAULT 'unchecked'
        CHECK (evidence_status IN ('unchecked','checked','insufficient')),
    relation_basis TEXT NOT NULL CHECK (
        relation_basis IN ('source_fact','semantic_inference')
    ),
    next_hop_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    policy_version TEXT,
    source_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (from_concept_id, to_concept_id, relation_type),
    CHECK (from_concept_id <> to_concept_id),
    CHECK (
        relation_type <> 'RELATED'
        OR from_concept_id < to_concept_id
    )
);

CREATE INDEX idx_concept_relation_target
ON public.concept_relation(to_concept_id);

COMMIT;
