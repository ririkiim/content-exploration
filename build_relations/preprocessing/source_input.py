"""원본을 보존하며 기존 설명·키워드와 위키 문단을 분리한다."""
import re
from .wiki_filter import build_inputs
from .context_compact import compact_context

CRITIQUE = ("통찰", "걸작", "극찬", "호평", "찬사", "베스트셀러", "판매량")


def clean_book_description(base):
    original = str(base.get("description") or "")
    if base["content_type"] != "book":
        return dict(base), []
    records, pieces = [], []
    # 문장을 다시 쓰지 않고 원문 구간만 선별한다.
    for match in re.finditer(r"[^\n]+?(?:[.!?。！？](?=\s|$)|$)", original):
        text = match.group().strip()
        if not text:
            continue
        flags = [word for word in CRITIQUE if word in text]
        selected = not flags
        records.append({"text": text, "selected": selected,
                        "reason": "critique_quote_review" if flags else "content_sentence",
                        "matched_terms": flags})
        if selected:
            pieces.append(text)
    cleaned = {**base, "description": "\n\n".join(pieces)}
    return cleaned, records


def build_source_input(context, base, allow_unlinked=False, wiki_max_chars=None):
    cleaned, decisions = clean_book_description(base)
    results = build_inputs(context, cleaned, allow_unlinked=allow_unlinked)
    # 형식 절도 사실·해석이 섞여 있으므로 새 입력 경로에서는 기본 제외한다.
    audit = results["audit"]
    for p in audit["paragraphs"]:
        if p["source_kind"] == "form":
            p.update(selected_for_llm=False, selection_reason="interpretation_separate")
    audit["sources"] = [s for s in audit["sources"] if s.get("source_kind") != "form"]
    combined = [s["text"] for s in audit["sources"]]
    offset = 0
    for s in audit["sources"]:
        s.update(description_start=offset, description_end=offset + len(s["text"]))
        offset += len(s["text"]) + 2
    results["enriched"]["description"] = "\n\n".join(combined)
    results = compact_context(results, wiki_max_chars=wiki_max_chars)
    audit = results["audit"]
    wiki = []
    source_records = []
    for s in audit["sources"]:
        source = {k: v for k, v in s.items() if k not in {"description_start", "description_end"}}
        if s.get("origin") == "wikipedia":
            source["input_field"] = "wiki_paragraphs"
            wiki.append({k: source.get(k) for k in (
                "source_id", "source_kind", "section", "text", "source_url", "revision_id"
            )})
        else:
            source["input_field"] = "description"
        source_records.append(source)

        # 형식·테마·주제는 직접 근거가 아닌 참고 자료로 전달한다.
    reference = []

    for p in audit.get("reference_paragraphs", []):
        reference.append({
            "source_id": p["source_id"],
            "source_kind": p["source_kind"],
            "section": p["section"],
            "text": p["text"],
            "source_url": p.get("source_url"),
            "revision_id": p.get("revision_id"),
        })

    payload = {
        **results["baseline"],
        "wiki_paragraphs": wiki,
        "wiki_reference_paragraphs": reference,
        "source_schema_version": "separated-wiki-v3-reference",
    }
    audit.update(
        policy_version="separated-wiki-v2-context",
        sources=source_records,
        original_catalogue_snapshot=base,
        catalogue_sentence_decisions=decisions,
        interpretation_policy="form/theme/evaluation excluded from default input",
    )
    audit["counts"] = {
        "description_chars": len(payload["description"]),
        "wiki_chars": sum(len(p["text"]) for p in wiki),
        "wiki_passages": len(wiki),
        "excluded_catalogue_sentences": sum(not d["selected"] for d in decisions),
        "reference_chars": sum(len(p["text"]) for p in reference),
        "reference_passages": len(reference),
    }
    return payload, audit
