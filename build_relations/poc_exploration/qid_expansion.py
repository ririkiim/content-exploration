"""선택한 QID의 한 단계 정방향 관계만 펼친다. DB/LLM 호출 없음."""
from datetime import datetime, timezone
from .identity import stable_id
from .policy import entity_kind
from .qid_policy import PROPERTIES, classify
from .qid_store import check_qid
from ..wikidata.client import claim_qids, label


def new_session():
    return {"schema_version": "qid-expansion-v1", "nodes": {}, "edges": {}, "steps": []}


def reachable(edges, start, end):
    pending, seen = [start], set()
    while pending:
        node = pending.pop()
        if node == end:
            return True
        if node in seen:
            continue
        seen.add(node)
        pending.extend(e["target_qid"] for e in edges.values() if e["source_qid"] == node)
    return False


def expand_one(qid, store, session, max_edges=60):
    check_qid(qid)
    if max_edges < 1:
        raise ValueError("max_edges는 양수여야 합니다.")
    previous = session["nodes"].get(qid, {})
    if previous.get("expansion_status") in {"expanded", "held_type", "missing"}:
        return {"qid": qid, "status": "already_processed", "previous_status": previous["expansion_status"], "new_edges": 0}
    entity = store.get(qid)
    if not entity:
        session["nodes"][qid] = {"qid": qid, "expansion_status": "missing", "kind": "unresolved"}
        step = {"qid": qid, "status": "missing", "new_edges": 0}
        session["steps"].append(step)
        return step
    classification = classify(entity, store)
    node = {"qid": qid, "name": label(entity), "kind": classification["kind"],
            "classification": classification, "source_url": f"https://www.wikidata.org/wiki/{qid}",
            "source_revision_id": entity.get("lastrevid"), **store.provenance(qid)}
    session["nodes"][qid] = node
    properties = PROPERTIES.get(classification["kind"], {})
    if not properties:
        node["expansion_status"] = "held_type"
        step = {"qid": qid, "status": "held_type", "classification": classification, "new_edges": 0}
        session["steps"].append(step)
        return step
    rows, skipped = [], []
    for prop, relation in properties.items():
        for claim in entity.get("claims", {}).get(prop, []):
            snak = claim.get("mainsnak", {})
            value = snak.get("datavalue", {}).get("value")
            if claim.get("rank") == "deprecated" or snak.get("snaktype") != "value" or not isinstance(value, dict) or not value.get("id", "").startswith("Q"):
                skipped.append({"property_id": prop, "claim_id": claim.get("id"), "reason": "deprecated_or_non_qid_value"})
                continue
            check_qid(value["id"])
            rows.append((prop, relation, value["id"], claim))
    # 같은 주어·속성·목적어의 claim은 하나의 관계에 근거를 모은다.
    groups = {}
    for prop, relation, target, claim in rows:
        groups.setdefault((prop, relation, target), []).append(claim)
    selected = list(groups.items())[:max_edges]
    targets = store.get_many(key[2] for key, _ in selected)
    added = 0
    for (prop, relation, target), claims in selected:
        target_entity = targets.get(target)
        target_node = session["nodes"].setdefault(target, {
            "qid": target, "name": label(target_entity) if target_entity else target,
            "kind": entity_kind(claim_qids(target_entity, "P31")) if target_entity else "unresolved",
            "expansion_status": "not_selected", "type_status": "preliminary_direct_P31",
            "target_available": bool(target_entity),
        })
        edge_id = stable_id(qid, prop, target)
        closes_cycle = target == qid or reachable(session["edges"], target, qid)
        evidence = []
        for claim in claims:
            evidence.append({"source_kind": "wikidata", "source_url": f"https://www.wikidata.org/wiki/{qid}#{prop}",
                "source_revision_id": entity.get("lastrevid"), "claim_id": claim.get("id"),
                "rank": claim.get("rank"), "has_reference": bool(claim.get("references")),
                "references": claim.get("references", []), "qualifiers": claim.get("qualifiers", {}),
                "raw_claim": claim, **store.provenance(qid)})
        session["edges"][edge_id] = {"edge_id": edge_id, "source_qid": qid, "property_id": prop,
            "relation": relation, "target_qid": target, "status": "source_asserted",
            "review_status": "proposed", "verified": False, "evidence": evidence,
            "generation_path": "code_wikidata_forward_claim",
            "self_loop": target == qid, "closes_cycle": closes_cycle,
            "target_already_processed": target_node.get("expansion_status") in {"expanded", "held_type", "missing"}}
        added += 1
    node["expansion_status"] = "expanded"
    step = {"qid": qid, "status": "expanded", "kind": classification["kind"], "new_edges": added,
            "eligible_edges": len(groups), "deferred_edges": len(groups) - len(selected), "skipped_claims": skipped,
            "selected_at": datetime.now(timezone.utc).isoformat(), "auto_recursive_expansion": False}
    session["steps"].append(step)
    return step
