"""작품 ID로 위키 매칭·수집. 애매하면 연결하지 않고 DB 입력으로 계속한다."""
import json
import re
from ..wikidata.repository import load_content
from ..wikidata.matching import match_content
from ..wikidata.enrichment import collect_wikidata
from ..wikipedia.wikipedia_client import fetch_wikipedia, WIKIPEDIA_API
from ..wikidata.client import request_json
from .identity import normalize


def select_qid(content, result):
    qid = result.get("candidate_qid")
    if not qid:
        return None
    rows = [c for c in result.get("candidates", []) if c["qid"] == qid]
    valid = []
    for row in rows:
        types = {t["qid"] for t in row["types"]}
        kind = content["content_type"]
        type_ok = bool(types & ({"Q11424"} if kind == "movie" else {"Q5398426"} if kind == "tv" else {"Q571", "Q7725634", "Q8261"}))
        title_ok = row.get("title_match")
        author_ok = row.get("author_match") if kind == "book" else True
        years = {int(m.group(1)) for v in row.get("publication_dates", []) if (m := re.match(r"\+(\d{4})-", v))}
        year_ok = kind == "book" or not years or not content.get("release_year") or content["release_year"] in years
        if type_ok and title_ok and author_ok and year_ok:
            valid.append(qid)
    return qid if valid else None


def validate_page(content, wd, page):
    """기존/신규 문서 모두 동일 기준으로 확인한다. 실패 시 사용을 보류한다."""
    if page.get("status") != "success":
        return {"accepted": False, "reason": page.get("status", "not_collected")}
    qid = wd.get("qid")
    page_qid = page.get("page_qid")
    if page_qid and page_qid != qid:
        return {"accepted": False, "reason": "qid_mismatch"}
    title = page.get("title", "")
    core = re.sub(r"\s*\([^)]*\)$", "", title)
    if page_qid and qid == page_qid:
        return {"accepted": True, "reason": "qid_match"}
    if qid and title == wd.get("kowiki_title") and normalize(wd.get("label", "")) == normalize(content["title"]):
        return {"accepted": True, "reason": "wikidata_sitelink_match"}
    if normalize(core) != normalize(content["title"]):
        return {"accepted": False, "reason": "title_mismatch"}
    intro = page.get("text", "").split("\n\n")[0]
    if content["content_type"] == "book" and not page_qid:
        authors = content.get("authors", [])
        if (authors and any(author in intro for author in authors)
                and any(word in intro for word in ("소설", "시집", "수필집", "저서", "책이다"))
                and not re.search(r"(?:영화|드라마)(?:이다|다)", intro)):
            return {"accepted": True, "reason": "title_author_book_kind_match",
                    "review_note": "문서 QID는 없으며 제목·저자·도서 유형을 코드로 대조함"}
    return {"accepted": False, "reason": "insufficient_identity_evidence"}


def find_unlinked_book(content, qid):
    search = request_json(WIKIPEDIA_API, {"action": "query", "format": "json", "list": "search",
        "srsearch": content["title"], "srlimit": 5, "srnamespace": 0})
    matches = []
    for candidate in search.get("query", {}).get("search", []):
        title = candidate["title"]
        core = re.sub(r"\s*\([^)]*\)$", "", title)
        if normalize(core) != normalize(content["title"]):
            continue
        page = fetch_wikipedia(title, expected_qid=qid)
        decision = validate_page(content, {"qid": qid}, page)
        if decision["accepted"]:
            page.update(selection_method="search_identity_rules", identity_selection_basis=decision["reason"])
            matches.append(page)
    return matches[0] if len(matches) == 1 else {"status": "ambiguous" if matches else "not_found", "reason": "한국어 문서의 제목·저자·유형 확인 실패 또는 복수 후보"}


def ensure_context(cid, context_dir):
    path = context_dir / f"context_{cid}.json"
    content = load_content(cid)
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("content", {}).get("content_id") == cid and cached.get("wikipedia", {}).get("status") == "success":
            decision = validate_page(content, cached.get("wikidata", {}), cached["wikipedia"])
            if decision["accepted"]:
                return {"status": "cached", "path": str(path), "identity_decision": decision}
    payload = {"content": content, "wikidata": {}, "wikipedia": {"status": "not_collected"}}
    try:
        result = match_content(content)
        payload["matching_result"] = result
        qid = select_qid(content, result)
        if not qid:
            payload["wikipedia"] = {"status": "match_unconfirmed", "match_status": result["status"], "reason": "단일 작품의 제목·유형·저자·연도를 확인하지 못함"}
        else:
            payload["wikidata"] = collect_wikidata(qid)
            payload["match"] = {"qid": qid, "method": result["method"], "selection": "code_checked"}
            title = payload["wikidata"].get("kowiki_title")
            page = fetch_wikipedia(title, expected_qid=qid)
            page["selection_method"] = "wikidata_sitelink"
            if not title and content["content_type"] == "book":
                page = find_unlinked_book(content, qid)
            decision = validate_page(content, payload["wikidata"], page)
            page["identity_decision"] = decision
            if page.get("status") == "success" and not decision["accepted"]:
                page["collection_status"] = "success"
                page["status"] = "identity_unconfirmed"
            payload["wikipedia"] = page
    except Exception as error:
        payload["wikipedia"] = {"status": "lookup_failed", "error": f"{type(error).__name__}: {error}"}
    context_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": payload["wikipedia"]["status"], "path": str(path)}
