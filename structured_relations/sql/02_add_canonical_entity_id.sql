--1. entity 테이블에 entity_canonical_id 컬럼 추가

BEGIN;

ALTER TABLE public.entity
ADD COLUMN canonical_entity_id BIGINT;

ALTER TABLE public.entity
ADD CONSTRAINT fk_entity_canonical
FOREIGN KEY (canonical_entity_id)
REFERENCES public.entity(entity_id)
ON DELETE RESTRICT;

ALTER TABLE public.entity
ADD CONSTRAINT chk_entity_canonical_not_self
CHECK (canonical_entity_id <> entity_id);

CREATE INDEX idx_entity_canonical_id
ON public.entity(canonical_entity_id)
WHERE canonical_entity_id IS NOT NULL;

COMMIT;

--2. entity_relation 테이블 제약 조건 수정 ('wirtten_by' 추가)
BEGIN;

ALTER TABLE public.entity_relation
DROP CONSTRAINT concept_relation_relation_type_check;

ALTER TABLE public.entity_relation
ADD CONSTRAINT concept_relation_relation_type_check
CHECK (
    relation_type IN (
        'BROADER',
        'RELATED',
        'TEMPORAL_WITHIN',
        'LOCATED_IN',
        'WRITTEN_BY'
    )
);

COMMIT;