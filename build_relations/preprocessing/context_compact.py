"""Wikipedia 원문 문맥을 작품 유형별 예산에 맞춰 선별한다.

- 영화: 2,000자
- 드라마·책: 4,000자
- 원문 문장을 재작성하거나 요약하지 않는다.
- 선택 및 제외 이유와 원문 위치를 감사 기록에 남긴다.
"""

import re

from .compact import grams, similarity, sentences


WIKI_LIMITS = {
    "movie": {"max_chars": 2000, "max_passages": 8},
    "tv": {"max_chars": 4000, "max_passages": 12},
    "book": {"max_chars": 4000, "max_passages": 12},
}

METADATA = (
    "개봉", "방영되", "방송했던",
    "시청률", "스트리밍", "한국 표준시",
)

DEPENDENT_START = re.compile(
    r"^(그러나|하지만|그래서|따라서|이에|그때|그럼에도|이후|이러한|그러한)"
)


def context_units(text, max_unit_chars=450):
    """원문 위치를 유지하면서 문맥 단위를 생성한다."""

    spans = list(sentences(text))

    clean = [
        span for span in spans
        if not any(word in span[2] for word in METADATA)
    ]

    if not clean:
        return

    groups = []

    for span in clean:
        gap = text[groups[-1][-1][1]:span[0]] if groups else ""

        if groups and not gap.strip():
            groups[-1].append(span)
        else:
            groups.append([span])

    for group in groups:
        if group[-1][1] - group[0][0] <= max_unit_chars:
            yield (
                group[0][0],
                group[-1][1],
                text[group[0][0]:group[-1][1]],
            )
            continue

        cursor = 0

        while cursor < len(group):
            end = min(len(group), cursor + 3)

            while (
                end > cursor + 1
                and group[end - 1][1] - group[cursor][0] > max_unit_chars
            ):
                end -= 1

            left = cursor

            while left > 0 and DEPENDENT_START.match(group[left][2]):
                left -= 1

            if (
                end < len(group)
                and DEPENDENT_START.match(group[end][2])
            ):
                end += 1

            start = group[left][0]
            stop = group[end - 1][1]

            yield start, stop, text[start:stop]

            cursor = end


def compact_context(results, wiki_max_chars=None, max_passages=None):
    """Wikipedia 문맥을 작품 유형에 맞춰 선별한다."""

    audit = results["audit"]

    content_type = results["baseline"]["content_type"]

    if content_type not in WIKI_LIMITS:
        raise ValueError(f"지원하지 않는 콘텐츠 유형: {content_type}")

    limits = WIKI_LIMITS[content_type]

    # 별도 설정이 없으면 콘텐츠 유형별 기본값 사용
    if wiki_max_chars is None:
        wiki_max_chars = limits["max_chars"]

    if max_passages is None:
        max_passages = limits["max_passages"]

    audit["compression"] = {
        "method": "context_units_v3",
        "applied": True,
        "content_type": content_type,
        "wiki_max_chars": wiki_max_chars,
        "max_passages": max_passages,
        "note": (
            "원문 문맥 단위 선별. "
            "의미적 요약이나 사실 검증은 수행하지 않음."
        ),
    }

    base = results["baseline"]["description"]

    base_grams = grams(base)

    base_sentences = [
        sentence
        for _, _, sentence in sentences(base)
    ]

    candidates = []

    for source_index, source in enumerate(audit["sources"]):

        if source.get("origin") != "wikipedia":
            continue

        unit_max_chars = (
            200
            if source["source_kind"] in {"intro", "overview"}
            else 450
        )

        for unit_index, (start, end, text) in enumerate(
            context_units(
                source["text"],
                max_unit_chars=unit_max_chars,
            )
        ):
            g = grams(text)

            novelty = len(g - base_grams) / max(1, len(g))

            overlap = max(
                (
                    similarity(text, sentence)
                    for sentence in base_sentences
                ),
                default=0,
            )

            # 기존 0.78보다 중복 판정을 완화
            reason = (
                "overlaps_baseline"
                if overlap > 0.90
                else "not_selected"
            )

            if len(text) > wiki_max_chars:
                reason = "context_unit_exceeds_budget"

            candidates.append({
                "parent_source_id": source["source_id"],
                "source": source,
                "start": start,
                "end": end,
                "text": text,
                "order": (source_index, unit_index),
                "novelty": round(novelty, 4),
                "baseline_overlap": round(overlap, 4),
                "selected": False,
                "reason": reason,
            })

    selected = []
    used = 0

    # 영화의 경우 짧은 배경·도입 문맥 하나를 우선 확보
    anchors = [
        c for c in candidates
        if c["reason"] == "not_selected"
        and c["source"]["source_kind"]
        in {"intro", "overview", "background"}
        and len(c["text"]) <= min(220, wiki_max_chars)
    ]

    if anchors and content_type == "movie":
        anchor = min(anchors, key=lambda c: c["order"])

        anchor.update(
            selected=True,
            reason="context_anchor",
        )

        selected.append(anchor)
        used = len(anchor["text"])

    while len(selected) < max_passages:

        eligible = []

        for c in candidates:

            if c["reason"] != "not_selected":
                continue

            overlap = max(
                (
                    similarity(c["text"], old["text"])
                    for old in selected
                ),
                default=0,
            )

            # 기존 0.68보다 중복 판정을 완화
            if overlap > 0.85:
                c["reason"] = "overlaps_selected"
                continue

            cost = len(c["text"]) + (2 if selected else 0)

            if used + cost > wiki_max_chars:
                continue

            score = (
                c["novelty"]
                - 0.65 * c["baseline_overlap"]
                - 0.85 * overlap
            )

            if (
                any(
                    word in c["text"]
                    for word in ("때문", "이유", "원인")
                )
                and any(
                    word in c["text"]
                    for word in ("그러나", "하지만", "따라서", "그래서")
                )
            ):
                score += 0.4

            if c["source"]["source_kind"] == "background":
                score += 0.1

            eligible.append((score, c))

        if not eligible:
            break

        _, chosen = max(
            eligible,
            key=lambda item: (
                item[0],
                tuple(-i for i in item[1]["order"]),
            ),
        )

        chosen.update(
            selected=True,
            reason="context_selected",
        )

        used += len(chosen["text"]) + (2 if selected else 0)

        selected.append(chosen)

    for c in candidates:
        if c["reason"] == "not_selected":
            c["reason"] = "budget_or_passage_limit"

    selected.sort(key=lambda c: c["order"])

    audit["paragraph_selection_sources"] = audit["sources"]

    sources = [
        s for s in audit["sources"]
        if s.get("origin") != "wikipedia"
    ]

    parts = [base] if base else []

    offset = len(base)

    for c in selected:

        source = c["source"]

        begin = offset + (2 if parts else 0)
        offset = begin + len(c["text"])

        sources.append({
            **source,
            "source_id": (
                source["source_id"]
                + f":chars{c['start']}-{c['end']}"
            ),
            "parent_source_id": source["source_id"],
            "text": c["text"],
            "raw_start": source["raw_start"] + c["start"],
            "raw_end": source["raw_start"] + c["end"],
            "description_start": begin,
            "description_end": offset,
        })

        parts.append(c["text"])

    audit["sources"] = sources

    audit["context_selection"] = [
        {
            k: v
            for k, v in c.items()
            if k != "source"
        }
        for c in candidates
    ]

    results["enriched"]["description"] = "\n\n".join(parts)

    return results