-- =========================================================
-- Migration: Update structured relation roles
-- Date: 2026-11-15
--
-- Purpose:
-- 1. Add WORK entity type
-- 2. Add screenwriter, author, original_author, original_story
-- 3. Rename based_on to original_story
--
-- Target DB: PostgreSQL / content_exploration
-- Run once per database after backup
-- =========================================================


BEGIN;

-- 1. 원작 작품을 위한 엔티티 유형 추가
ALTER TABLE public.entity
DROP CONSTRAINT concept_concept_type_check;

ALTER TABLE public.entity
ADD CONSTRAINT concept_concept_type_check
CHECK (
    entity_type IN (
        'PERSON',
        'EVENT',
        'PLACE',
        'ORGANIZATION',
        'TOPIC',
        'KEYWORD',
        'TIME_PERIOD',
        'METADATA',
        'WORK',
        'GENRE'
    )
);

-- 2. 관계 역할을 임시 확장
-- 기존 based_on과 신규 original_story를 모두 허용
ALTER TABLE public.content_entity
DROP CONSTRAINT content_concept_link_role_check;

ALTER TABLE public.content_entity
ADD CONSTRAINT content_concept_link_role_check
CHECK (
    link_role IN (
        'subject',
        'depicts',
        'setting',
        'director',
        'cast',
        'based_on',
        'screenwriter',
        'author',
        'original_author',
        'original_story',
        'genre'
    )
);

ALTER TABLE public.content_entity_candidate
DROP CONSTRAINT content_concept_candidate_link_role_check;

ALTER TABLE public.content_entity_candidate
ADD CONSTRAINT content_concept_candidate_link_role_check
CHECK (
    link_role IN (
        'subject',
        'depicts',
        'setting',
        'director',
        'cast',
        'based_on',
        'screenwriter',
        'author',
        'original_author',
        'original_story',
        'genre'
    )
);

-- 3. 기존 based_on 데이터를 신규 명칭으로 변경
UPDATE public.content_entity
SET link_role = 'original_story'
WHERE link_role = 'based_on';

UPDATE public.content_entity_candidate
SET link_role = 'original_story'
WHERE link_role = 'based_on';

-- 4. 최종 제약조건 적용
ALTER TABLE public.content_entity
DROP CONSTRAINT content_concept_link_role_check;

ALTER TABLE public.content_entity
ADD CONSTRAINT content_concept_link_role_check
CHECK (
    link_role IN (
        'subject',
        'depicts',
        'setting',
        'director',
        'cast',
        'screenwriter',
        'author',
        'original_author',
        'original_story',
        'genre'
    )
);

ALTER TABLE public.content_entity_candidate
DROP CONSTRAINT content_concept_candidate_link_role_check;

ALTER TABLE public.content_entity_candidate
ADD CONSTRAINT content_concept_candidate_link_role_check
CHECK (
    link_role IN (
        'subject',
        'depicts',
        'setting',
        'director',
        'cast',
        'screenwriter',
        'author',
        'original_author',
        'original_story',
        'genre'
    )
);


COMMIT;