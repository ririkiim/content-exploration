"""Wikipedia 원문 문단을 분류하고 입력·감사 기록을 생성한다.

원칙
- Wikipedia 원문을 요약하거나 재작성하지 않는다.
- 줄거리·배경 등은 직접 근거 후보로 사용한다.
- 형식·테마·주제는 참고 자료 후보로 별도 보존한다.
- 긴 문단은 원문 위치를 유지하면서 분할한다.
- 기존 build_inputs() 반환 구조를 유지한다.
"""

import re

from .policy import (
    VERSION,
    SECTION_KINDS,
    SEPARATE_SECTIONS,
    PROMOTION_TERMS,
)


REFERENCE_SECTIONS = {
    "형식",
    "테마",
    "주제",
}

REFERENCE_KINDS = {
    "form",
    "theme",
}

# 원문에서 문단을 분할하기 시작하는 길이
SPLIT_THRESHOLD = 2000


def split_paragraphs(text):
    """기존 Wikipedia 평문을 절·문단 단위로 분리한다."""

    headings = list(
        re.finditer(
            r"\n{3,}([^\n]{1,100})(?=\n|\Z)",
            text,
        )
    )

    sections = [
        (
            "도입부",
            0,
            headings[0].start() if headings else len(text),
        )
    ]

    for index, match in enumerate(headings):
        end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(text)
        )

        sections.append(
            (
                match.group(1).strip(),
                match.end(),
                end,
            )
        )

    for section_index, (section, start, end) in enumerate(sections):
        block = text[start:end]

        for paragraph_index, match in enumerate(
            re.finditer(r"[^\n]+", block)
        ):
            raw = match.group(0)
            body = raw.rstrip()

            yield {
                "section": section,
                "section_index": section_index,
                "paragraph_index": paragraph_index,
                "raw_start": start + match.start(),
                "raw_end": start + match.start() + len(body),
                "text": body,
            }


def split_long_paragraph(paragraph, max_chars=2000):
    """긴 문단을 원문 위치를 유지하면서 분할한다.

    문장 경계를 우선 사용한다.
    한 문장이 제한보다 길면 문자 단위로 분할한다.
    """

    text = paragraph["text"]

    if len(text) <= max_chars:
        yield {
            **paragraph,
            "chunk_index": 0,
            "chunk_count": 1,
        }
        return

    # 문장 종료 기호 뒤의 공백을 경계로 사용
    boundaries = [
        0,
        *[
            match.end()
            for match in re.finditer(
                r"(?<=[.!?。！？])\s+",
                text,
            )
        ],
        len(text),
    ]

    boundaries = sorted(set(boundaries))

    chunks = []
    cursor = 0

    while cursor < len(text):
        limit = min(cursor + max_chars, len(text))

        possible = [
            boundary
            for boundary in boundaries
            if cursor < boundary <= limit
        ]

        end = max(possible) if possible else limit

        # 앞뒤 공백을 제외하되 원문 위치를 정확히 기록
        segment = text[cursor:end]

        leading = len(segment) - len(segment.lstrip())
        trailing = len(segment.rstrip())

        chunk_start = cursor + leading
        chunk_end = cursor + trailing

        if chunk_end > chunk_start:
            chunks.append(
                {
                    **paragraph,
                    "text": text[chunk_start:chunk_end],
                    "raw_start": paragraph["raw_start"] + chunk_start,
                    "raw_end": paragraph["raw_start"] + chunk_end,
                }
            )

        cursor = end

    for index, chunk in enumerate(chunks):
        yield {
            **chunk,
            "chunk_index": index,
            "chunk_count": len(chunks),
        }


def classify_paragraph(section, kind):
    """Wikipedia 절의 역할을 분류한다."""

    if section in REFERENCE_SECTIONS or kind in REFERENCE_KINDS:
        return "reference"

    if kind in {
        "intro",
        "plot",
        "overview",
        "background",
    }:
        return "direct_evidence"

    return "excluded"


def build_inputs(
    context,
    base,
    max_chars=6000,
    max_paragraph_chars=2000,
    allow_unlinked=False,
):
    """Wikipedia 직접 근거와 참고 자료 후보를 분리한다."""

    content = context["content"]

    if int(base["content_id"]) != int(content["content_id"]):
        raise ValueError(
            "기본 입력과 위키 자료의 content_id가 다릅니다."
        )

    if base.get("content_type") != content["content_type"]:
        raise ValueError(
            "기본 입력의 작품 유형이 다릅니다."
        )

    wp = context.get("wikipedia", {})
    expected = context["wikidata"]["qid"]
    page_qid = wp.get("page_qid")

    if page_qid and page_qid != expected:
        raise ValueError(
            "Wikipedia 문서의 QID가 다릅니다."
        )

    manual_unlinked = (
        wp.get("selection_method") == "manual_title"
        and not page_qid
    )

    if manual_unlinked and not allow_unlinked:
        raise ValueError(
            "수동 확인한 미연결 문서는 --allow-unlinked를 지정하세요."
        )

    if wp.get("status") != "success":
        raise ValueError(
            f"Wikipedia 수집 상태를 확인하세요: {wp.get('status')}"
        )

    if not wp.get("page_id") or not wp.get("revision_id"):
        raise ValueError(
            "문서 ID와 revision ID가 필요합니다."
        )

    a = {
        k: base.get(k)
        for k in (
            "content_id",
            "content_type",
            "title",
            "release_year",
            "genres",
            "keywords",
        )
    }

    a["description"] = str(
        base.get("description") or ""
    )

    a["genres"] = a["genres"] or []
    a["keywords"] = a["keywords"] or []

    b = {**a}

    pieces = (
        [a["description"]]
        if a["description"]
        else []
    )

    used = len(a["description"])

    sources = []

    if pieces:
        sources.append(
            {
                "source_id": (
                    f"catalogue:{content['content_id']}:description"
                ),
                "source_kind": "catalogue_description",
                "origin": (
                    base.get("description_source")
                    or "catalogue_mixed_or_unknown"
                ),
                "text": a["description"],
                "description_start": 0,
                "description_end": used,
            }
        )

    paragraphs = []
    reference_paragraphs = []

    seen = set(pieces)

    text = wp.get("text", "")

    for original in split_paragraphs(text):

        section = original["section"]

        kind = SECTION_KINDS.get(
            section,
            "other",
        )

        role = classify_paragraph(
            section,
            kind,
        )

        # 직접 근거와 참고 자료는 같은 방식으로 분할
        for p in split_long_paragraph(
            original,
            max_chars=max_paragraph_chars,
        ):

            body = p["text"]

            selected = False
            reason = None

            if role == "reference":
                reason = "reference_only"

            elif role == "excluded":
                reason = (
                    "separate_interpretation_or_production"
                    if section in SEPARATE_SECTIONS
                    else "excluded_or_unknown_section"
                )

            elif (
                kind in {"intro", "overview"}
                and any(
                    term in body
                    for term in PROMOTION_TERMS
                )
            ):
                reason = "promotion_or_reception_paragraph"

            elif body in seen:
                reason = "exact_duplicate"

            elif (
                used
                + (2 if pieces else 0)
                + len(body)
                > max_chars
            ):
                reason = "total_limit"

            else:
                selected = True
                reason = "included_section"

            source_id = (
                f"wikipedia:{wp['page_id']}:{wp['revision_id']}:"
                f"s{p['section_index']}:"
                f"p{p['paragraph_index']}"
            )

            if p["chunk_count"] > 1:
                source_id += f":chunk{p['chunk_index']}"

            record = {
                **p,
                "source_id": source_id,
                "source_kind": kind,
                "source_role": role,
                "origin": "wikipedia",
                "source_url": wp.get("source_url"),
                "page_id": wp["page_id"],
                "revision_id": wp["revision_id"],
                "selected_for_llm": selected,
                "selection_reason": reason,
                "verification_role": (
                    "expansion_only"
                    if role == "direct_evidence"
                    else "reference_only"
                    if role == "reference"
                    else "excluded"
                ),
            }

            if selected:
                begin = (
                    used
                    + (2 if pieces else 0)
                )

                used = begin + len(body)

                record.update(
                    description_start=begin,
                    description_end=used,
                )

                sources.append(dict(record))
                pieces.append(body)
                seen.add(body)

            if role == "reference":
                reference_paragraphs.append(
                    dict(record)
                )

            paragraphs.append(record)

    b["description"] = "\n\n".join(pieces)

    return {
        "baseline": a,
        "enriched": b,
        "audit": {
            "policy_version": VERSION,
            "mode": "experiment",
            "content_id": content["content_id"],
            "wikipedia": {
                k: v
                for k, v in wp.items()
                if k != "text"
            },
            "maintenance_check": (
                "not_performed_plaintext_only"
            ),
            "warnings": (
                ["baseline_exceeds_limit"]
                if len(a["description"]) > max_chars
                else []
            ),
            "limits": {
                "description_chars": max_chars,
                "paragraph_chars": max_paragraph_chars,
            },
            "counts": {
                "baseline_chars": len(a["description"]),
                "enriched_chars": len(b["description"]),
                "paragraphs": len(paragraphs),
                "selected_paragraphs": sum(
                    p["selected_for_llm"]
                    for p in paragraphs
                ),
                "reference_paragraphs": len(
                    reference_paragraphs
                ),
            },
            "keywords_raw": (
                base.get("keywords") or []
            ),
            "sources": sources,
            "paragraphs": paragraphs,
            "reference_paragraphs": reference_paragraphs,
        },
    }