import unicodedata

from .client import (
    claim_qids,
    claim_values,
    get_entities,
    label,
    lookup_id,
    names,
    search_title,
)


def normalize(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(value.casefold().split())


def find_candidates(content):
    sources = content["sources"]
    content_type = content["content_type"]

    if sources.get("wikidata"):
        return sorted(set(sources["wikidata"])), "existing_mapping"

    if content_type in {"movie", "tv"}:
        source, prop = {
            "movie": ("tmdb_movie", "P4947"),
            "tv": ("tmdb_tv", "P4983"),
        }[content_type]

        ids = sorted(set(sources.get(source, [])))

        if len(ids) > 1:
            raise ValueError(f"{source} ID가 여러 개입니다: {ids}")

        if not ids:
            return [], "missing_tmdb_id"

        return lookup_id(prop, ids[0]), "tmdb"

    if content_type == "book":
        if content.get("isbn13"):
            qids = lookup_id("P212", content["isbn13"])
            if qids:
                return qids, "isbn13"

        if not content["authors"]:
            return [], "missing_author"

        return search_title(content["title"]), "title_author"

    raise ValueError(f"지원하지 않는 작품 유형: {content_type}")


def inspect_candidates(content, qids):
    entities = get_entities(qids)
    is_book = content["content_type"] == "book"

    # ISBN으로 찾은 판본 → 연결된 작품 후보
    work_qids = {
        work_qid
        for entity in entities.values()
        for work_qid in claim_qids(entity, "P629")
    } if is_book else set()

    entities.update(get_entities(work_qids))

    related_qids = {
        target
        for entity in entities.values()
        for prop in ("P31", "P50")
        for target in claim_qids(entity, prop)
    }
    related = get_entities(related_qids)

    expected_titles = {
        normalize(value)
        for value in (content["title"], content.get("original_title"))
        if value
    }
    expected_authors = {
        normalize(author) for author in content["authors"]
    }

    candidates = []

    for source_qid in qids:
        source_entity = entities.get(source_qid)
        if source_entity is None:
            continue

        works = claim_qids(source_entity, "P629") if is_book else []

        for qid in works or [source_qid]:
            entity = entities.get(qid)
            if entity is None:
                continue

            author_qids = claim_qids(entity, "P50")
            author_names = {
                normalize(name)
                for author_qid in author_qids
                for name in names(related.get(author_qid, {}))
            }

            candidates.append({
                "qid": qid,
                "source_qid": source_qid,
                "edition_qid": source_qid if works else None,
                "title": label(entity),
                "description": (
                    entity.get("descriptions", {})
                    .get("ko", {}).get("value")
                    or entity.get("descriptions", {})
                    .get("en", {}).get("value")
                ),
                "types": [
                    {
                        "qid": target,
                        "label": label(related.get(target, {})) or target,
                    }
                    for target in claim_qids(entity, "P31")
                ],
                "authors": [
                    label(related.get(target, {})) or target
                    for target in author_qids
                ],
                "publication_dates": sorted({
                    value["time"]
                    for value in claim_values(entity, "P577")
                    if isinstance(value, dict) and value.get("time")
                }),
                "title_match": bool(
                    expected_titles & {
                        normalize(name) for name in names(entity)
                    }
                ),
                "author_match": bool(expected_authors & author_names),
                "url": f"https://www.wikidata.org/wiki/{qid}",
            })

    return candidates


def match_content(content):
    qids, method = find_candidates(content)
    candidates = inspect_candidates(content, qids) if qids else []

    eligible = candidates
    if method == "title_author":
        eligible = [
            candidate
            for candidate in candidates
            if candidate["title_match"] and candidate["author_match"]
        ]

    unique_qids = sorted({candidate["qid"] for candidate in eligible})

    if len(unique_qids) == 1:
        status = "review_required"
    elif len(unique_qids) > 1:
        status = "ambiguous"
    else:
        status = "unmatched"

    return {
        "status": status,
        "method": method,
        "candidate_qid": unique_qids[0] if len(unique_qids) == 1 else None,
        "eligible_qids": unique_qids,
        "candidates": candidates,
    }
