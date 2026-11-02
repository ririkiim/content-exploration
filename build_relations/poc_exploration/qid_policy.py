"""엔티티의 유형별 정방향 관계와 근거가 있는 제한적 상위 유형 확인."""
from .policy import TYPE_QIDS, FICTIONAL_QIDS, entity_kind
from ..wikidata.client import claim_qids

# 기존 작품 관계는 유지. 추상 토픽 확장/역방향 검색은 포함하지 않는다.
PROPERTIES = {
    "work": {"P50": "authored_by", "P57": "directed_by", "P58": "written_by",
             "P170": "created_by", "P161": "cast_member", "P144": "adapted_from",
             "P180": "depicts", "P921": "has_subject", "P840": "narrative_location", "P793": "significant_event"},
    "event": {"P361": "part_of", "P527": "has_part", "P710": "participant",
              "P276": "location", "P17": "country"},
    "person": {"P1344": "participant_in", "P607": "conflict", "P463": "member_of", "P800": "notable_work"},
    "organization": {"P361": "part_of", "P527": "has_part", "P17": "country"},
    "place": {"P131": "located_in", "P17": "country", "P361": "part_of"},
}


def classify(entity, store, max_depth=3, max_classes=60):
    direct = claim_qids(entity, "P31")
    if claim_qids(entity, "P279") and entity_kind(direct) == "unresolved":
        return {"kind": "unresolved", "status": "unresolved", "direct_type_qids": direct,
                "type_paths": [], "reason": "type_or_concept_not_concrete_instance"}
    layer = [(qid, [entity["id"], qid]) for qid in direct]
    visited, found, paths = set(), set(), []
    capped = False
    for depth in range(max_depth + 1):
        following = []
        # 분류용 경로는 탐색 관계와 섞지 않는다.
        for qid, path in layer:
            if qid in visited:
                continue
            if len(visited) >= max_classes:
                capped = True
                break
            visited.add(qid)
            kinds = [kind for kind, roots in TYPE_QIDS.items() if qid in roots]
            if qid in FICTIONAL_QIDS:
                kinds = ["fictional"]
            for kind in kinds:
                found.add(kind)
                paths.append({"kind": kind, "qids": path,
                              "properties": ["P31"] + ["P279"] * (len(path) - 2)})
            if kinds:
                continue
            if depth < max_depth:
                parent_entity = store.get(qid)
                if parent_entity:
                    following.extend((p, path + [p]) for p in claim_qids(parent_entity, "P279") if p not in visited)
        if not following or capped:
            break
        layer = following
    kind = "fictional" if "fictional" in found else next(iter(found)) if len(found) == 1 else "unresolved"
    return {"kind": kind, "status": "classified" if kind != "unresolved" else "unresolved",
            "direct_type_qids": direct, "type_paths": paths, "bounded_search": True,
            "max_depth": max_depth, "max_classes": max_classes, "capped": capped,
            "reason": "conflicting_types" if len(found) > 1 and kind == "unresolved" else "type_not_resolved" if kind == "unresolved" else "P31_P279_evidence"}
