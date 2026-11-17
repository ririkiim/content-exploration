"""출처/인용·알려진 항목 중복·출력 역할을 코드로 검사한다."""
import json
import re
from extract_content_topics import parse_response, validate_evidence_quote, find_quote
from semantic_prompt import prepare_input
from topic_normalization import normalization_candidate, interpretation_review_flags
from build_relations.exploration.identity import normalize


def validate_wiki_response(raw, content):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    data = json.loads(text)
    if len(data.get("topics", [])) > 15:
        raise ValueError("최대 15개 토픽 제한을 초과했습니다.")
    topics = parse_response(text, content["content_id"])
    prepared = prepare_input(content)
    sources = {"description": prepared["description"]}
    wiki_ids = {p["source_id"] for p in prepared["wiki_paragraphs"]}
    sources.update({p["source_id"]: p["text"] for p in prepared["wiki_paragraphs"]})
    known = {normalize(row["name"]) for row in prepared["known_items"]}
    seen = set()
    for raw_topic, topic in zip(data["topics"], topics):
        declared = raw_topic.get("evidence_source")
        if not isinstance(declared, str):
            raise ValueError("evidence_source가 없습니다.")
        valid_source = declared in sources
        validation = validate_evidence_quote(topic, [(declared, sources[declared])] if valid_source else [])
        topic.update(validation)
        topic["declared_evidence_source"] = declared
        matches = [name for name, value in sources.items() if find_quote(topic["evidence_quote"], [(name, value)])]
        exact = valid_source and bool(topic["evidence_quote"]) and topic["evidence_quote"] in sources[declared]
        flags, exclusions = topic["review_flags"], []
        if not valid_source:
            flags.append("unknown_evidence_source")
        if not exact:
            exclusions.append("exact_quote_required")
        if topic["quote_valid"] and not exact:
            flags.append("normalized_match_only")
        if any(word in topic["evidence_quote"] for word in ("통찰", "탐구", "성찰", "걸작", "호평")):
            flags.append("critique_quote_review")
        key = normalize(topic["name"])
        if key in known:
            exclusions.append("already_known_item")
        if key in seen:
            exclusions.append("duplicate_topic_name")
        seen.add(key)
        if topic["type"] == "subject":
            exclusions.append("factual_or_subject_output")
        if topic["confidence"] == "low":
            exclusions.append("low_confidence")
        topic.update(
            source_matches=matches, exact_quote=exact,
            wiki_evidence=declared in wiki_ids,
            wiki_only_quote=bool(matches) and all(name in wiki_ids for name in matches),
            known_name_match=key in known,
            semantic_novelty_status="not_reviewed",
            candidate_status="proposed", semantic_review_status="not_reviewed",
            quote_role="expansion_material" if declared in wiki_ids else "catalogue_material",
            eligible_for_review=not exclusions, exclusion_reasons=exclusions,
        )
        topic["normalization_candidate"] = normalization_candidate(topic)
        topic["generation_path"] = "llm_semantic_expansion"
        flags.extend(interpretation_review_flags(topic))
        flags.extend(exclusions)
    return topics
