from datetime import datetime, timezone

from ..wikidata.client import request_json

WIKIPEDIA_API = "https://ko.wikipedia.org/w/api.php"


def fetch_wikipedia(title, expected_qid=None):
    if not title:
        return {
            "status": "no_sitelink",
            "reason": "Wikidata에 한국어 Wikipedia 연결이 없습니다.",
        }

    data = request_json(
        WIKIPEDIA_API,
        {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "titles": title,
            "redirects": 1,
            "prop": "extracts|info|pageprops",
            "explaintext": 1,
            "exsectionformat": "plain",
            "inprop": "url",
        },
    )

    pages = data.get("query", {}).get("pages", [])

    if not pages or "missing" in pages[0]:
        return {
            "status": "not_found",
            "requested_title": title,
        }

    page = pages[0]

    if "disambiguation" in page.get("pageprops", {}):
        return {
            "status": "disambiguation",
            "title": page["title"],
            "source_url": page.get("fullurl"),
            "reason": "동음이의어 문서이므로 본문을 사용하지 않습니다.",
        }

    page_qid = page.get("pageprops", {}).get("wikibase_item")

    # 다른 작품의 QID에 연결돼 있으면 본문을 사용하지 않는다.
    if expected_qid and page_qid and page_qid != expected_qid:
        return {
            "status": "qid_mismatch",
            "requested_title": title,
            "title": page["title"],
            "source_url": page.get("fullurl"),
            "expected_qid": expected_qid,
            "page_qid": page_qid,
            "reason": "문서가 다른 Wikidata 개체에 연결돼 있습니다.",
        }

    text = page.get("extract", "").strip()

    return {
        "status": "success" if text else "empty",
        "requested_title": title,
        "title": page["title"],
        "page_id": page["pageid"],
        "revision_id": page.get("lastrevid"),
        "source_url": page.get("fullurl"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "expected_qid": expected_qid,
        "page_qid": page_qid,
        "identity_check": (
            "qid_match" if expected_qid and page_qid == expected_qid
            else "unlinked" if not page_qid
            else "not_checked"
        ),
        "text": text,
    }