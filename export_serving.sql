-- export_serving.sql : 로컬 DB에서 실행 (읽기만 함). 현재 폴더에 CSV 11개 생성
-- 실행: psql -v ON_ERROR_STOP=1 -d <로컬DB명> -f export_serving.sql
\set ON_ERROR_STOP on
-- 같은 시점의 데이터를 보도록 한 트랜잭션(읽기 전용, 스냅샷)으로 묶는다
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
\copy (select * from public.content) to 'serving_content.csv' with (format csv, header true, encoding 'UTF8')
\copy (select * from public.movie_detail) to 'serving_movie_detail.csv' with (format csv, header true, encoding 'UTF8')
\copy (select * from public.tv_detail) to 'serving_tv_detail.csv' with (format csv, header true, encoding 'UTF8')
\copy (select * from public.book_edition) to 'serving_book_edition.csv' with (format csv, header true, encoding 'UTF8')
\copy (select * from public.content_source_map) to 'serving_content_source_map.csv' with (format csv, header true, encoding 'UTF8')
\copy (select * from public.entity) to 'serving_entity.csv' with (format csv, header true, encoding 'UTF8')
\copy (select * from public.content_keyword) to 'serving_content_keyword.csv' with (format csv, header true, encoding 'UTF8')
\copy (select * from public.entity_alias where status='verified') to 'serving_entity_alias.csv' with (format csv, header true, encoding 'UTF8')
\copy (select * from public.content_entity where fact_status='verified') to 'serving_content_entity.csv' with (format csv, header true, encoding 'UTF8')
\copy (select ev.* from public.content_entity_evidence ev join public.content_entity ce using (content_entity_id) where ce.fact_status='verified') to 'serving_content_entity_evidence.csv' with (format csv, header true, encoding 'UTF8')
\copy (select * from public.entity_relation where fact_status='verified') to 'serving_entity_relation.csv' with (format csv, header true, encoding 'UTF8')
COMMIT;
