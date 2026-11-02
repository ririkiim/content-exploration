"""영화 KMDb 키워드를 우선 보존하고 QID 식별 후보를 별도로 붙인다."""
import hashlib
import json
from .identity import normalize
from .qid_policy import classify
from .policy import entity_kind
from ..wikidata.client import search_title, names, label, claim_qids


def collect_movie_keywords(bundle):
    if bundle["content"]["content_type"] != "movie":
        raise ValueError("KMDb 우선 처리는 영화에만 적용합니다.")
    cid = bundle["content"]["content_id"]
    source = [s for s in bundle.get("sources", []) if s["source_name"] == "kmdb_movie"]
    raw = bundle.get("kmdb_raw") or {}
    groups = {}
    for i, item in enumerate(bundle.get("detail", {}).get("keywords") or []):
        if not isinstance(item, dict) or item.get("source") not in {"kmdb", "kmdb_movie"}:
            continue
        name = item.get("name", "")
        key = normalize(name)
        if not key:
            continue
        group = groups.setdefault(key, {"name": name, "kind": "unclassified_keyword",
            "content_id": cid, "source_kind": "kmdb_keyword", "relation": "tagged_with",
            "review_status": "proposed", "match_status": "not_attempted", "qid": None,
            "evidence": []})
        group["evidence"].append({"field": f"movie_detail.keywords[{i}]", "original": item,
            "source_records": source, "raw_row_id": raw.get("raw_kmdb_movie_id")})
    return {"content_id": cid, "content_type": "movie", "title": bundle["content"]["title"],
        "primary_route": "kmdb_keywords_then_qid", "keywords": list(groups.values()),
        "raw_kmdb_keywords": raw.get("raw_payload", {}).get("키워드"),
        "raw_field_needs_parsing": bool(raw.get("raw_payload", {}).get("키워드")) and not groups,
        "supplementary_candidates": [], "verified": False}


def contextual_targets(context):
    wd = (context or {}).get("wikidata", {})
    raw = wd.get("raw_work", {})
    targets = wd.get("raw_targets", {})
    # 작품의 주제/묘사/사건 연결만 이름 식별을 보강한다. 출연 배우를 서사 인물로 오인하지 않는다.
    output = {}
    for prop in ("P921", "P180", "P793", "P840", "P144"):
        for qid in claim_qids(raw, prop):
            if qid in targets:
                output[qid] = {"entity": targets[qid], "property_id": prop,
                    "source_qid": wd.get("qid"), "source_url": wd.get("source_url")}
    return output


def resolve_keyword(keyword, store, context=None):
    result = dict(keyword)
    key = normalize(keyword["name"])
    hints = contextual_targets(context)
    hinted = [qid for qid, row in hints.items() if any(normalize(n) == key for n in names(row["entity"]))]
    if hinted:
        qids = sorted(hinted)
        method = "name_with_structured_context"
    else:
        folder = store.folder / "keyword_searches"
        folder.mkdir(exist_ok=True)
        path = folder / (hashlib.sha256(keyword["name"].encode()).hexdigest() + ".json")
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached["query"] != keyword["name"]:
                raise ValueError("키워드 검색 캐시 불일치")
            qids = cached["qids"]
        else:
            qids = search_title(keyword["name"])
            with path.open("x", encoding="utf-8") as file:
                json.dump({"query": keyword["name"], "qids": qids}, file, ensure_ascii=False, indent=2)
        method = "exact_label_or_alias_search"
    entities = store.get_many(qids)
    exact = {qid: entity for qid, entity in entities.items() if entity and any(normalize(n) == key for n in names(entity))}
    result.update(match_method=method, candidate_qids=sorted(exact), candidate_labels={q: label(e) for q,e in exact.items()},
        candidate_details=[{"qid": q, "label": label(e), "descriptions": e.get("descriptions", {}),
            "direct_type_qids": claim_qids(e,"P31"), "subclass_of": claim_qids(e,"P279"),
            "kind_hint": entity_kind(claim_qids(e,"P31")),
            "category_hint": "type_or_concept_candidate" if claim_qids(e,"P279") else "instance_or_unresolved",
            "source_snapshot": store.provenance(q)} for q,e in exact.items()])
    if len(exact) != 1:
        result.update(match_status="ambiguous" if exact else "no_exact_match", classification_status="unresolved")
        return result
    qid, entity = next(iter(exact.items()))
    # 상위 분류 자체인 대상을 실존 사건/인물로 오인하지 않는다.
    if claim_qids(entity, "P279") and entity_kind(claim_qids(entity, "P31")) == "unresolved":
        result.update(match_status="review_required", qid_candidate=qid,
            kind="type_or_concept_candidate", classification_status="not_concrete_instance",
            classification_evidence={"P31": claim_qids(entity,"P31"),"P279":claim_qids(entity,"P279")})
        return result
    classification = classify(entity, store)
    concrete = classification["kind"] in {"person", "event", "place", "organization", "work"}
    # 이름 하나의 일치만으로 작품 속 대상을 확정하지 않는다.
    corroborated = qid in hints
    result.update(qid_candidate=qid, kind=classification["kind"], classification=classification,
        classification_status=classification["status"], match_status="matched_context_candidate" if concrete and corroborated else "review_required",
        qid=qid if concrete and corroborated else None, identity_verified=False,
        identity_note="원본 키워드와 문맥 연결을 보존한 식별 후보. 묘사 관계나 서비스 승인은 별개.",
        context_evidence=hints.get(qid), source_snapshot=store.provenance(qid))
    return result
