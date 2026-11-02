
CREATE OR REPLACE FUNCTION public.normalize_keyword(x TEXT)
RETURNS TEXT
LANGUAGE sql
STABLE
RETURNS NULL ON NULL INPUT
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

