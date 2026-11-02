"""구조화 필드·키워드·Wikidata claim을 모델 없이 추출한다."""
from .identity import normalize, stable_id, merge_assertions
from .policy import RELATIONS, entity_kind
from ..wikidata.client import claim_qids, label


def collect_inventory(bundle, context):
    content, detail = bundle["content"], bundle["detail"]
    cid, ctype = content["content_id"], content["content_type"]
    assertions, tags, metadata = [], [], []
    source_urls = {s["source_name"]: s.get("source_url") for s in bundle["sources"]}

    def add(name, kind, relation, source, field, value, ids=None, extra=None, scope=None):
        if not name:
            return
        assertions.append({
            "assertion_id": stable_id(cid, source, field, relation, ids or name),
            "content_id": cid, "relation": relation,
            "target": {"name": name, "kind": kind, "external_ids": ids or {}},
            "identity_scope": scope or f"{source}:content:{cid}",
            "status": "source_asserted", "verified": False,
            "evidence": [{"source": source, "field": field, "value": value,
                          "source_url": source_urls.get(source), **(extra or {})}],
        })

    for field, relation in (("directors", "directed_by"), ("creators", "created_by"), ("cast_members", "cast_member")):
        for index, row in enumerate(detail.get(field) or []):
            source = row.get("source") or ("tmdb_tv" if ctype == "tv" else "catalogue_unknown")
            source = {"kmdb": "kmdb_movie", "tmdb": "tmdb_movie"}.get(source, source)
            pid = row.get("person_id", row.get("id"))
            ids = {"tmdb_person": str(pid)} if pid is not None and source.startswith("tmdb") else {}
            add(row.get("name"), "person", relation, source, f"{field}[{index}]", row, ids)
    for index, row in enumerate(detail.get("contributors") or []):
        if row.get("role") == "author":
            add(row.get("name"), "person", "authored_by", "data4library_book",
                f"contributors[{index}]", row)
    for field, relation, namespace in (("networks", "aired_on", "tmdb_network"),
                                       ("production_companies", "produced_by", "tmdb_company")):
        for index, row in enumerate(detail.get(field) or []):
            ids = {namespace: str(row["id"])} if row.get("id") is not None else {}
            add(row.get("name"), "organization", relation, "tmdb_tv" if ctype == "tv" else "tmdb_movie",
                f"{field}[{index}]", row, ids)
    raw = bundle.get("kmdb_raw")
    if raw:
        for field, relation in (("각본", "written_by"),):
            value = raw["raw_payload"].get(field)
            if value:
                # 구분자를 추측해 이름을 깨지 않는다. 복합 값은 검토 후보로 보존.
                add(value, "unresolved", relation, "kmdb_movie", field, value,
                    extra={"raw_row_id": raw["raw_kmdb_movie_id"], "review_required": True})

    for field in ("genres", "keywords"):
        for index, item in enumerate(detail.get(field) or []):
            row = item if isinstance(item, dict) else {"name": str(item)}
            source = row.get("source") or ("tmdb_tv" if ctype == "tv" else "catalogue_unknown")
            source = {"kmdb": "kmdb_movie", "tmdb": "tmdb_movie"}.get(source, source)
            tags.append({
                "name": row.get("name", ""), "normalized_name": normalize(row.get("name", "")),
                "kind": "genre" if field == "genres" else "source_keyword",
                "status": "source_tag", "entity_type": "unresolved",
                "source": source, "field": f"{field}[{index}]", "source_id": row.get("id"),
                "original": row, "relation_claim": "tagged_with",
            })

    wd = context.get("wikidata", {})
    work = wd.get("raw_work", {})
    targets = wd.get("raw_targets", {})
    for prop, claims in work.get("claims", {}).items():
        if prop not in RELATIONS and prop not in {"P31", "P136"}:
            continue
        for index, claim in enumerate(claims):
            if claim.get("rank") == "deprecated":
                continue
            snak = claim.get("mainsnak", {})
            value = snak.get("datavalue", {}).get("value")
            if snak.get("snaktype") != "value" or not isinstance(value, dict) or not value.get("id"):
                continue
            qid = value["id"]
            target = targets.get(qid, {})
            name = label(target) or qid
            if prop in {"P31", "P136"}:
                metadata.append({"property_id": prop, "name": name, "qid": qid})
                continue
            kind = entity_kind(claim_qids(target, "P31"))
            if prop in {"P50", "P57", "P58", "P170", "P161"}:
                kind = "person" if kind == "unresolved" else kind
            add(name, kind, RELATIONS[prop], "wikidata", f"claims.{prop}[{index}]", value,
                {"wikidata": qid}, extra={
                    "source_url": wd.get("source_url"), "property_id": prop,
                    "claim_id": claim.get("id"), "rank": claim.get("rank"),
                    "has_reference": bool(claim.get("references")),
                    "references": claim.get("references", []), "qualifiers": claim.get("qualifiers", {}),
                    "target_type_qids": claim_qids(target, "P31"),
                }, scope="wikidata")
    for field in ("release_date", "first_air_date", "last_air_date", "pub_year"):
        if detail.get(field) is not None:
            metadata.append({"field": field, "value": str(detail[field]),
                             "role": "edition_publication_time" if field == "pub_year" else "release_or_broadcast_time",
                             "source": "catalogue", "not_narrative_time": True})
    assertions = merge_assertions(assertions)
    # 표시 우선 출처만 정한다. 다른 출처의 주장·근거는 삭제하지 않는다.
    for a in assertions:
        if ctype == "tv" and a["relation"] in {"created_by", "directed_by", "written_by"}:
            a["display_relation"] = "created_by"
        else:
            a["display_relation"] = a["relation"]
        primary = "kmdb_movie" if ctype == "movie" else "tmdb_tv" if ctype == "tv" else "data4library_book"
        same_relation = [other for other in assertions if other["relation"] == a["relation"]]
        primary_exists = any(e["source"] == primary for other in same_relation for e in other["evidence"])
        a["preferred_for_display"] = not primary_exists or any(e["source"] == primary for e in a["evidence"])
    # 키워드와 동일 표기의 구조화 대상은 식별 참고만 연결한다. 이름만으로 ID를 합치지 않는다.
    for tag in tags:
        tag["identity_candidates"] = [a["target"]["external_ids"] for a in assertions
            if normalize(a["target"]["name"]) == tag["normalized_name"]]
    keyword_groups = {}
    for tag in tags:
        key = (tag["kind"], tag["normalized_name"])
        if not key[1]:
            continue
        group = keyword_groups.setdefault(key, {"name": tag["name"], "kind": tag["kind"],
            "status": "source_tag", "evidence": [], "identity_candidates": []})
        group["evidence"].append(tag)
        for candidate in tag["identity_candidates"]:
            if candidate not in group["identity_candidates"]:
                group["identity_candidates"].append(candidate)
    return {"content_id": cid, "assertions": assertions,
            "source_tags": list(keyword_groups.values()), "metadata": metadata,
            "identity_policy": "ID-based merge; same-name cross-source matches remain candidates"}


def known_items(inventory, link_candidates=(), context=None):
    from .known_selection import select_known_items
    return select_known_items(inventory, link_candidates, context)[0]


def add_link_identification(inventory, links):
    """같은 이름의 링크 식별 결과는 후보로 연결한다. 이름만으로 개체를 합치지 않는다."""
    entities = []
    for a in inventory["assertions"]:
        if (a["target"]["kind"] in {"person", "event", "organization", "place", "work"}
                or (a["target"]["kind"] == "unresolved"
                    and a["target"].get("external_ids", {}).get("wikidata"))):
            entities.append({"target": a["target"], "relation": a["relation"],
                             "evidence": a["evidence"], "status": "proposed",
                             "identification_method": "structured_source",
                             "classification_status": "unresolved" if a["target"]["kind"] == "unresolved" else "classified"})
    for link in links:
        if (link.get("candidate_kind") in {"entity", "work"}
                or (link.get("candidate_kind") == "unresolved" and link.get("target_qid"))):
            entities.append({"target": {"name": link["canonical_title"], "kind": link["entity_kind"],
                "external_ids": {"wikidata": link["target_qid"]} if link.get("target_qid") else {}},
                "relation": link["relation"], "evidence": [link["evidence"]],
                "status": "proposed", "identification_method": "wikipedia_link_and_type"})
    for tag in inventory["source_tags"]:
        for link in links:
            if link.get("candidate_kind") not in {"entity", "work"} or not link.get("target_qid"):
                continue
            if normalize(tag["name"]) in {normalize(link["target_title"]), normalize(link["canonical_title"]), normalize(link["label"])}:
                ids = {"wikidata": link["target_qid"]}
                if ids not in tag["identity_candidates"]:
                    tag["identity_candidates"].append(ids)
                tag["identification_status"] = "candidate_name_match"
    inventory["entity_candidates"] = entities
    inventory["unresolved_tags"] = [tag["name"] for tag in inventory["source_tags"]
                                    if tag["kind"] == "source_keyword" and not tag["identity_candidates"]]
    return inventory
