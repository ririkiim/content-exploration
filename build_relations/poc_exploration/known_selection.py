"""LLM용 목록만 선별한다. 원본 inventory와 관계는 변경하지 않는다."""
import re
from .identity import normalize
from ..preprocessing.wiki_filter import split_paragraphs

CREDIT_RELATIONS = {"cast_member", "directed_by", "written_by", "created_by", "authored_by", "produced_by", "aired_on"}
STORY_RELATIONS = {"depicts", "has_subject", "adapted_from", "narrative_location", "significant_event"}
NARRATIVE_SECTIONS = {"도입부", "개요", "줄거리", "내용", "시놉시스", "배경", "역사적 배경", "사건 개요"}
# 평가 작품에 맞춘 번역·동의어 치환은 사용하지 않는다.
ALIASES = {}


def clean_name(name):
    name = " ".join(str(name or "").split())
    return ALIASES.get(name, name)


def name_variants(*names):
    values = set()
    for name in names:
        if name:
            values.add(name)
            values.add(re.sub(r"\s*\([^)]*\)$", "", name))
    return {v for v in values if v}


def narrative_occurrences(context, variants):
    matches = []
    for paragraph in split_paragraphs(context.get("wikipedia", {}).get("text", "")):
        if paragraph["section"] not in NARRATIVE_SECTIONS:
            continue
        for text in re.split(r"(?<=[.!?。])\s+|\n+", paragraph["text"]):
            if any(word in text for word in ("개봉", "출연", "감독", "제작사", "스트리밍", "방영", "주연", "제작되", "장편소설이다")):
                # 도입부의 제작/방송 설명과 배우 표기를 후보 이름의 서사 등장으로 간주하지 않는다.
                if paragraph["section"] in {"도입부", "개요"}:
                    continue
            for name in variants:
                start = text.find(name)
                if start < 0:
                    continue
                left = text[max(0, start - 12):start]
                right = text[start + len(name):start + len(name) + 10]
                parenthetical = text.rfind("(", 0, start) > text.rfind(")", 0, start)
                if parenthetical or re.search(r"배우\s*$", left) or re.match(r"\s*(?:감독|배우|작가|프로듀서)", right):
                    continue
                matches.append({"section": paragraph["section"], "text": text, "matched_name": name})
    return matches


def select_known_items(inventory, links=(), context=None):
    context = context or {}
    credit_names, credit_qids = set(), set()
    story_names, story_qids = set(), set()
    rows = []
    for a in inventory.get("assertions", []):
        target = a["target"]
        variants = name_variants(target["name"])
        qid = target.get("external_ids", {}).get("wikidata")
        if a["relation"] in CREDIT_RELATIONS:
            credit_names.update(normalize(n) for n in variants)
            if qid:
                credit_qids.add(qid)
        if a["relation"] in STORY_RELATIONS:
            story_names.update(normalize(n) for n in variants)
            if qid:
                story_qids.add(qid)
            rows.append({"name": target["name"], "kind": target["kind"], "origin": "structured_story_relation", "qid": qid})
    rows[:0] = [{"name": tag["name"], "kind": tag["kind"], "origin": "source_tag"}
               for tag in inventory.get("source_tags", [])]
    for link in links:
        if link.get("candidate_kind") not in {"work", "entity"}:
            continue
        variants = name_variants(link.get("label"), link.get("canonical_title"), link.get("target_title"))
        occurrences = narrative_occurrences(context, variants)
        qid = link.get("target_qid")
        explicit_story = qid in story_qids or any(normalize(n) in story_names for n in variants)
        credited = qid in credit_qids or any(normalize(n) in credit_names for n in variants)
        rows.append({"name": link.get("canonical_title") or link.get("label") or link["target_title"], "kind": link.get("entity_kind", "unresolved"),
                     "origin": "wikipedia_link", "qid": qid, "credited": credited,
                     "explicit_story": explicit_story, "narrative_occurrences": occurrences})
    decisions, groups = [], {}
    for row in rows:
        name = clean_name(row["name"])
        reason = None
        if not name or re.fullmatch(r"Q\d+", name):
            reason = "unidentified_qid_or_empty"
        elif row["kind"] == "genre":
            reason = "genre_not_needed"
        elif row["origin"] == "wikipedia_link":
            if not row["explicit_story"] and not row["narrative_occurrences"]:
                reason = "credit_or_metadata_only" if row["credited"] else "no_narrative_role_found"
        key = next((k for k, g in groups.items() if normalize(g["name"]) == normalize(name)
                    or (row.get("qid") and any(e.get("qid") == row["qid"] for e in g["evidence_rows"]))), normalize(name))
        if reason:
            decisions.append({**row, "selected": False, "reason": reason})
            continue
        group = groups.setdefault(key, {"name": name, "kind": row["kind"], "evidence_rows": []})
        group["evidence_rows"].append(row)
        decisions.append({**row, "selected": True, "selected_name": name,
                          "reason": "duplicate_surface_or_approved_alias" if len(group["evidence_rows"]) > 1 else "relevant_known_item"})
    selected = [{"name": g["name"], "kind": g["kind"]} for g in groups.values()]
    return selected, {"policy_version": "known-items-v2-role-based", "selected_count": len(selected),
                      "decisions": decisions, "groups": list(groups.values()),
                      "note": "LLM 목록 선별만 수행. 원본 식별자·관계·근거는 통합/삭제하지 않음."}
