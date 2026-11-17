"""영화·TV의 위키 보강만 문장 단위로 압축. 도서는 그대로 유지."""
import re


def grams(text):
    text = re.sub(r"[^가-힣a-z0-9]", "", text.casefold())
    return {text[i:i + 3] for i in range(max(0, len(text) - 2))}


def similarity(a, b):
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / max(1, min(len(ga), len(gb)))


def sentences(text):
    # 원문 구간을 그대로 보존. 짧은 날짜 조각은 다음 문장과 묶는다.
    start = 0
    for match in re.finditer(r"[.!?。！？](?:[’”\"])?(?=\s|$)", text):
        end = match.end()
        if len(text[start:end].strip()) < 30:
            continue
        left = start + len(text[start:end]) - len(text[start:end].lstrip())
        yield left, end, text[left:end]
        start = end
    if text[start:].strip():
        left = start + len(text[start:]) - len(text[start:].lstrip())
        yield left, len(text.rstrip()), text[left:].rstrip()


def compact_inputs(results, wiki_max_chars=650, max_sentences=5):
    audit = results["audit"]
    audit["compression"] = {
        "method": "extractive_sentences", "wiki_max_chars": wiki_max_chars,
        "max_sentences": max_sentences,
        "applied": results["baseline"]["content_type"] in {"movie", "tv"},
    }
    if not audit["compression"]["applied"]:
        return results

    base = results["baseline"]["description"]
    keywords = [k.get("name", "") if isinstance(k, dict) else str(k)
                for k in results["baseline"].get("keywords", [])]
    candidates = []
    for source_index, source in enumerate(audit["sources"]):
        if source.get("origin") != "wikipedia":
            continue
        for index, (start, end, text) in enumerate(sentences(source["text"])):
            # 공개일·작품 형식 소개는 이미 카탈로그에 있으므로 보강에서 제외
            metadata = any(word in text for word in ("개봉", "방영되", "방송했던", "시청률", "스트리밍"))
            near_base = max((similarity(text, s) for _, _, s in sentences(base)), default=0)
            keyword_hits = sum(len(k) >= 2 and k in text for k in keywords)
            signals = sum(word in text for word in ("갈등", "그러나", "하지만", "위해", "은폐", "진실", "편견", "항쟁", "독재", "관계", "윤리"))
            score = 1 + min(keyword_hits, 3) + min(signals, 2)
            if source["source_kind"] in {"intro", "background", "overview"}:
                score += 1
            score -= 3 * near_base
            candidates.append({
                "parent_source_id": source["source_id"],
                "source": source, "start": start, "end": end,
                "text": text, "order": (source_index, index), "score": score,
                "selected": False,
                "reason": "metadata" if metadata else "overlaps_baseline" if near_base > .65 else "not_selected",
            })
    chosen = []
    used = 0
    for candidate in sorted(candidates, key=lambda c: (-c["score"], c["order"])):
        if candidate["reason"] != "not_selected":
            continue
        if any(similarity(candidate["text"], c["text"]) > .65 for c in chosen):
            candidate["reason"] = "overlaps_selected"
            continue
        size = len(candidate["text"]) + (2 if chosen else 0)
        if len(chosen) >= max_sentences or used + size > wiki_max_chars:
            candidate["reason"] = "budget"
            continue
        candidate.update(selected=True, reason="selected")
        chosen.append(candidate)
        used += size
    chosen.sort(key=lambda c: c["order"])

    # 원래 문단 선택 결과도 보존하고, 실제 입력에는 선택 문장만 기록한다.
    audit["paragraph_selection_sources"] = audit["sources"]
    sources = [s for s in audit["sources"] if s.get("origin") != "wikipedia"]
    parts = [base] if base else []
    offset = len(base)
    for c in chosen:
        source = c["source"]
        begin = offset + (2 if parts else 0)
        offset = begin + len(c["text"])
        sources.append({
            **source,
            "source_id": source["source_id"] + f":chars{c['start']}-{c['end']}",
            "parent_source_id": source["source_id"],
            "text": c["text"],
            "raw_start": source["raw_start"] + c["start"],
            "raw_end": source["raw_start"] + c["end"],
            "description_start": begin, "description_end": offset,
        })
        parts.append(c["text"])
    results["enriched"]["description"] = "\n\n".join(parts)
    audit["sources"] = sources
    audit["sentence_selection"] = [{k: v for k, v in c.items() if k != "source"} for c in candidates]
    audit["counts"].update(
        enriched_chars=len(results["enriched"]["description"]),
        selected_sentences=len(chosen), wiki_chars=used,
    )
    return results
