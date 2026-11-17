"""해당 revision의 내부 링크를 코드로 수집. 관계 검증은 수행하지 않는다."""
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import unquote, quote, urlsplit, parse_qs
from ..wikidata.client import request_json, get_entities, claim_qids, claim_values
from ..wikipedia.wikipedia_client import WIKIPEDIA_API
from .policy import entity_kind

ALLOWED = {"도입부", "줄거리", "내용", "개요", "시놉시스", "배경", "역사적 배경", "사건 개요", "같이 보기"}


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.section = "도입부"
        self.heading = None
        self.heading_text = []
        self.anchor = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"h2", "h3", "h4", "h5", "h6"}:
            self.heading, self.heading_text = tag, []
        if tag == "a" and self.heading is None:
            href = attrs.get("href", "")
            if "redlink=1" in href:
                return
            if href.startswith("./"):
                title = unquote(href[2:].split("#")[0]).replace("_", " ")
            elif href.startswith("/wiki/"):
                title = unquote(href[6:].split("#")[0]).replace("_", " ")
            else:
                return
            if title and ":" not in title:
                self.anchor = {"section": self.section, "target_title": title, "label": ""}

    def handle_data(self, data):
        if self.heading:
            self.heading_text.append(data)
        if self.anchor:
            self.anchor["label"] += data

    def handle_endtag(self, tag):
        if tag == self.heading:
            self.section = "".join(self.heading_text).replace("[편집]", "").strip()
            self.heading = None
        if tag == "a" and self.anchor:
            if self.section in ALLOWED:
                self.links.append(self.anchor)
            self.anchor = None


def resolve_links(links):
    titles = sorted({link["target_title"] for link in links})
    resolved = {}
    for start in range(0, len(titles), 40):
        batch = titles[start:start + 40]
        data = request_json(WIKIPEDIA_API, {
            "action": "query", "format": "json", "formatversion": 2,
            "titles": "|".join(batch), "redirects": 1, "prop": "pageprops|info", "inprop": "url",
        })
        query = data.get("query", {})
        redirects = {r["from"]: r["to"] for r in query.get("normalized", []) + query.get("redirects", [])}
        pages = {page["title"]: page for page in query.get("pages", [])}
        for original in batch:
            canonical, seen = original, set()
            while canonical in redirects and canonical not in seen:
                seen.add(canonical)
                canonical = redirects[canonical]
            resolved[original] = pages.get(canonical, {})
    qids = {page.get("pageprops", {}).get("wikibase_item") for page in resolved.values()}
    entities = get_entities([q for q in qids if q])
    output = []
    for link in links:
        page = resolved.get(link["target_title"], {})
        props = page.get("pageprops", {})
        qid = props.get("wikibase_item")
        target = entities.get(qid, {})
        kind = entity_kind(claim_qids(target, "P31"))
        title = page.get("title", link["target_title"])
        if "목록" in title or page.get("ns", 0) != 0:
            candidate_kind, reason = "navigation", "list_or_namespace"
        elif "missing" in page or not page:
            candidate_kind, reason = "unresolved", "missing_page"
        elif "disambiguation" in props:
            candidate_kind, reason = "navigation", "disambiguation"
        elif kind == "fictional":
            candidate_kind, reason = "excluded", "fictional_entity"
        elif kind == "work":
            candidate_kind, reason = "work", "direct_instance_type"
        elif kind in {"person", "event", "organization", "place"}:
            candidate_kind, reason = "entity", "direct_instance_type"
        else:
            candidate_kind, reason = "unresolved", "type_not_confirmed"
        ids = {}
        for prop, key in (("P4947", "tmdb_movie"), ("P4983", "tmdb_tv")):
            values = claim_values(target, prop)
            if len(values) == 1 and isinstance(values[0], str):
                ids[key] = values[0]
        output.append({**link, "canonical_title": title, "page_id": page.get("pageid"),
            "target_qid": qid, "target_url": page.get("fullurl"), "external_ids": ids,
            "entity_kind": kind, "candidate_kind": candidate_kind, "classification_reason": reason,
            "relation": "see_also_candidate" if link["section"] == "같이 보기" else "mention_candidate",
            "status": "proposed", "verified": False})
    return output


def collect_links(wikipedia):
    revision = wikipedia.get("revision_id")
    if not revision:
        raise ValueError("revision ID가 없습니다.")
    data = request_json(WIKIPEDIA_API, {
        "action": "parse", "format": "json", "formatversion": 2,
        "oldid": revision, "prop": "text|templates|revid", "disableeditsection": 1,
    })
    if "error" in data:
        raise RuntimeError(str(data["error"]))
    parsed = data["parse"]
    if parsed.get("revid") != revision:
        raise ValueError("요청한 문서 revision과 응답이 다릅니다.")
    if parsed.get("pageid") != wikipedia.get("page_id"):
        raise ValueError("요청한 원본 문서와 응답 page ID가 다릅니다.")
    parser = LinkParser()
    parser.feed(parsed.get("text", ""))
    unique = {(link["section"], link["target_title"]): link for link in parser.links}
    templates = [t.get("title", "") for t in parsed.get("templates", [])]
    flags = [name for name in templates if any(token in name for token in ("출처 필요", "출처 부족", "출처 없음", "광고", "독자 연구", "독자연구"))]
    candidates = resolve_links(list(unique.values()))
    for c in candidates:
        c["evidence"] = {
            "source_kind": "wikipedia", "source_url": wikipedia.get("source_url"),
            "page_id": wikipedia.get("page_id"), "revision_id": revision, "section": c["section"],
            "anchor_text": c["label"], "verification_role": "candidate_discovery_only",
        }
    return {"source_page_id": wikipedia.get("page_id"), "source_revision_id": revision,
            "fetched_at": datetime.now(timezone.utc).isoformat(), "maintenance_flags": flags,
            "status": "success", "candidates": candidates}
