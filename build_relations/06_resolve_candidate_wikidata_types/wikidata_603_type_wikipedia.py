import re

from wikidata_601_type_config import (
    BATCH_SIZE,
    WIKIPEDIA_API,
)
from wikidata_602_type_utils import (
    batched,
    normalize_title,
    request_json,
)


def get_wikipedia_lookup_title(row):
    """
    실제 Wikipedia 링크 대상 제목을 우선 사용한다.
    없으면 entity_name을 대체 제목으로 사용한다.
    """
    evidence = row.get("evidence") or {}

    if isinstance(evidence, str):
        import json

        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = {}

    link_target_title = evidence.get("link_target_title")

    if isinstance(link_target_title, str) and link_target_title.strip():
        return link_target_title.strip()

    entity_name = row.get("entity_name")

    if isinstance(entity_name, str) and entity_name.strip():
        return entity_name.strip()

    return None


def resolve_titles_to_qids(titles):
    """
    한국어 Wikipedia 제목을 QID로 변환한다.

    반환:
        title_to_qid:
            정규화 제목 -> QID 또는 None
        disambiguation_titles:
            동음이의어 페이지로 확인된 정규화 제목 집합
    """
    unique_titles = list(dict.fromkeys(
        str(title).strip()
        for title in titles
        if title and str(title).strip()
    ))

    title_to_qid = {}
    disambiguation_titles = set()

    for batch in batched(unique_titles, BATCH_SIZE):
        data = request_json(
            WIKIPEDIA_API,
            {
                "action": "query",
                "format": "json",
                "prop": "pageprops",
                "ppprop": "wikibase_item|disambiguation",
                "redirects": 1,
                "titles": "|".join(batch),
            },
        )

        query = data.get("query", {})
        pages = query.get("pages", {})

        page_list = (
            list(pages.values())
            if isinstance(pages, dict)
            else pages
        )

        aliases = {}

        for item in query.get("normalized", []):
            aliases[normalize_title(item["from"])] = item["to"]

        for item in query.get("redirects", []):
            aliases[normalize_title(item["from"])] = item["to"]

        page_by_title = {
            normalize_title(page.get("title", "")): page
            for page in page_list
        }

        for requested_title in batch:
            requested_key = normalize_title(requested_title)
            canonical_title = requested_title
            visited = set()

            while True:
                key = normalize_title(canonical_title)

                if key in visited:
                    break

                visited.add(key)
                next_title = aliases.get(key)

                if not next_title:
                    break

                canonical_title = next_title

            page = page_by_title.get(normalize_title(canonical_title))

            if page is None:
                page = page_by_title.get(requested_key)

            if not page or page.get("missing"):
                title_to_qid[requested_key] = None
                continue

            pageprops = page.get("pageprops", {})

            if "disambiguation" in pageprops:
                disambiguation_titles.add(requested_key)
                title_to_qid[requested_key] = None
                continue

            qid = pageprops.get("wikibase_item")

            if qid and re.fullmatch(r"Q\d+", qid):
                title_to_qid[requested_key] = qid
            else:
                title_to_qid[requested_key] = None

    return title_to_qid, disambiguation_titles