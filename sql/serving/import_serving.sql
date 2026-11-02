-- import_serving.sql : GCP 서비스 DB에서 실행 (schema_serving.sql 복원 후)
-- 실행: psql -v ON_ERROR_STOP=1 -d <GCP DB명> -f import_serving.sql  (CSV가 있는 폴더에서)
\set ON_ERROR_STOP on
BEGIN;
TRUNCATE public.content, public.movie_detail, public.tv_detail, public.book_edition, public.content_source_map, public.entity, public.content_keyword, public.entity_alias, public.content_entity, public.content_entity_evidence, public.entity_relation;
\copy public.content from 'serving_content.csv' with (format csv, header true, encoding 'UTF8')
\copy public.movie_detail from 'serving_movie_detail.csv' with (format csv, header true, encoding 'UTF8')
\copy public.tv_detail from 'serving_tv_detail.csv' with (format csv, header true, encoding 'UTF8')
\copy public.book_edition from 'serving_book_edition.csv' with (format csv, header true, encoding 'UTF8')
\copy public.content_source_map from 'serving_content_source_map.csv' with (format csv, header true, encoding 'UTF8')
\copy public.entity from 'serving_entity.csv' with (format csv, header true, encoding 'UTF8')
\copy public.content_keyword from 'serving_content_keyword.csv' with (format csv, header true, encoding 'UTF8')
\copy public.entity_alias from 'serving_entity_alias.csv' with (format csv, header true, encoding 'UTF8')
\copy public.content_entity from 'serving_content_entity.csv' with (format csv, header true, encoding 'UTF8')
\copy public.content_entity_evidence from 'serving_content_entity_evidence.csv' with (format csv, header true, encoding 'UTF8')
\copy public.entity_relation from 'serving_entity_relation.csv' with (format csv, header true, encoding 'UTF8')
COMMIT;
ANALYZE;
-- 적재 검증: 행 수가 export 시점과 같은지 확인
SELECT 'content' t,count(*) FROM content UNION ALL SELECT 'content_keyword',count(*) FROM content_keyword UNION ALL SELECT 'entity',count(*) FROM entity UNION ALL SELECT 'entity_alias',count(*) FROM entity_alias UNION ALL SELECT 'content_entity',count(*) FROM content_entity UNION ALL SELECT 'content_entity_evidence',count(*) FROM content_entity_evidence UNION ALL SELECT 'entity_relation',count(*) FROM entity_relation;
