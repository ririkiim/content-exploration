"""출처 속성 매핑. 특정 작품 ID로 분기하지 않는다."""
RELATIONS = {
    "P50": "authored_by", "P57": "directed_by", "P58": "written_by",
    "P170": "created_by", "P161": "cast_member", "P144": "adapted_from",
    "P180": "depicts", "P921": "has_subject", "P840": "narrative_location",
    "P793": "significant_event",
}
# 직접 P31만으로 확인 가능한 유형. 상위 유형이 불명확하면 보류한다.
TYPE_QIDS = {
    "person": {"Q5"},
    "event": {"Q1190554", "Q1656682", "Q178561", "Q198", "Q40231"},
    "organization": {"Q43229", "Q4830453", "Q2088357"},
    "place": {"Q17334923", "Q2221906", "Q515", "Q6256", "Q82794"},
    "work": {"Q11424", "Q5398426", "Q571", "Q7725634", "Q47461344", "Q11060274", "Q7889"},
}
FICTIONAL_QIDS = {"Q95074", "Q15632617", "Q14897293"}


def entity_kind(types):
    types = set(types)
    if types & FICTIONAL_QIDS:
        return "fictional"
    matches = [kind for kind, ids in TYPE_QIDS.items() if types & ids]
    return matches[0] if len(matches) == 1 else "unresolved"
