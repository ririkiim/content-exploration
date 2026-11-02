
BEGIN;

-- =====================================================
-- 1. 영화 키워드 (KMDb / TMDb)
-- =====================================================

INSERT INTO public.content_keyword (
    content_id,
    source_name,
    source_keyword_id,
    keyword_name,
    keyword_normalized
)
SELECT
    m.content_id,
    lower(btrim(k.item->>'source')),
    NULLIF(btrim(k.item->>'id'), ''),
    btrim(k.item->>'name'),
    public.normalize_keyword(k.item->>'name')
FROM public.movie_detail m
CROSS JOIN LATERAL jsonb_array_elements(
    CASE
        WHEN jsonb_typeof(m.keywords) = 'array'
        THEN m.keywords
        ELSE '[]'::jsonb
    END
) AS k(item)
WHERE lower(btrim(k.item->>'source')) IN ('kmdb', 'tmdb')
  AND NULLIF(public.normalize_keyword(k.item->>'name'), '') IS NOT NULL
ON CONFLICT DO NOTHING;


-- =====================================================
-- 2. TV 키워드 (TMDb)
-- =====================================================

INSERT INTO public.content_keyword (
    content_id,
    source_name,
    source_keyword_id,
    keyword_name,
    keyword_normalized
)
SELECT
    t.content_id,
    'tmdb',
    NULLIF(btrim(k.item->>'id'), ''),
    btrim(k.item->>'name'),
    public.normalize_keyword(k.item->>'name')
FROM public.tv_detail t
CROSS JOIN LATERAL jsonb_array_elements(
    CASE
        WHEN jsonb_typeof(t.keywords) = 'array'
        THEN t.keywords
        ELSE '[]'::jsonb
    END
) AS k(item)
WHERE NULLIF(public.normalize_keyword(k.item->>'name'), '') IS NOT NULL
ON CONFLICT DO NOTHING;

COMMIT;
