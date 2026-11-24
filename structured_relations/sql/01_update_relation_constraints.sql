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

-- 1. 엔티티 유형 추가
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
        'genre',
        'represents_work'
    )
);


--2. 제약 조건 수정
CREATE UNIQUE INDEX uq_entity_genre_name
ON public.entity (entity_name)
WHERE entity_type = 'GENRE';

COMMIT;