
"""Wikipedia wikitext에서 탐색 후보와 근거 문맥을 추출한다."""

import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from common import normalize_name
from candidate_scoring import link_counts, score_candidates
from wikipedia_501_candidate_config import (
    EXCLUDED_SECTION_WORDS,
    GENERIC_LINKS,
    INFOBOX_FIELD_ALIASES,
    NOISE_PATTERNS,
    RELATED_WORK_WORDS,
    SECTION_RULES,
    BOX_OFFICE_WORDS,
    FUTURE_PROJECT_WORDS,
    MAX_CONTEXT_CHARS,
)


HEADING_PATTERN = re.compile(r"(?m)^(={2,6})\s*(.*?)\s*\1\s*$")

LINK_PATTERN = re.compile(
    r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]"
)

LINK_WITH_LABEL_PATTERN = re.compile(
    r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]"
)


WORK_TITLE_HINT = re.compile(
    r"\((?:\d{4}년\s+)?(?:영화|드라마|만화|웹툰|소설)\)$"
)
CREATOR_TITLE_HINT = re.compile(
    r"\((?:영화 감독|감독|연출가|작가|소설가|만화가|웹툰 작가)\)$"
)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize(value):
    return normalize_name(value)


def strip_wikitext(text):
    text = re.sub(r"(?s)<ref\b[^>]*>.*?</ref>", " ", text or "")
    text = re.sub(r"(?s)<ref\b[^>]*/\s*>", " ", text)
    text = re.sub(r"(?s)\{\|.*?\|\}", " ", text)

    # 중첩 템플릿을 여러 차례 제거한다.
    for _ in range(8):
        updated = re.sub(r"\{\{[^{}]*\}\}", " ", text)
        if updated == text:
            break
        text = updated

    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\]", " ", text)
    text = re.sub(r"'{2,5}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text, flags=re.I)

    return re.sub(r"[ \t]+", " ", text).strip()


def split_top_level(text, separator="|"):
    parts, start = [], 0
    template_depth = link_depth = 0
    i = 0

    while i < len(text):
        if text.startswith("{{", i):
            template_depth += 1
            i += 2
            continue

        if text.startswith("}}", i) and template_depth:
            template_depth -= 1
            i += 2
            continue

        if text.startswith("[[", i):
            link_depth += 1
            i += 2
            continue

        if text.startswith("]]", i) and link_depth:
            link_depth -= 1
            i += 2
            continue

        if (
            text[i] == separator
            and template_depth == 0
            and link_depth == 0
        ):
            parts.append(text[start:i])
            start = i + 1

        i += 1

    parts.append(text[start:])
    return parts


def top_level_templates(wikitext):
    templates, i = [], 0

    while i < len(wikitext):
        if not wikitext.startswith("{{", i):
            i += 1
            continue

        start, depth, j = i, 1, i + 2

        while j < len(wikitext) and depth:
            if wikitext.startswith("{{", j):
                depth += 1
                j += 2
            elif wikitext.startswith("}}", j):
                depth -= 1
                j += 2
            else:
                j += 1

        if depth == 0:
            templates.append(wikitext[start + 2:j - 2])
            i = j
        else:
            break

    return templates


def unwrap_templates(text):
    for _ in range(8):
        updated = re.sub(r"\{\{[^{}]*\}\}", " ", text)
        if updated == text:
            break
        text = updated

    return text


def infobox_plain_text(raw_value):
    value = unwrap_templates(raw_value)
    value = re.sub(r"<ref\b[^>]*/\s*>", " ", value, flags=re.I)
    value = re.sub(r"(?s)<ref\b[^>]*>.*?</ref>", " ", value, flags=re.I)
    return strip_wikitext(value)


def is_noise_link(name):
    name = clean_text(name)

    if not name or name in GENERIC_LINKS:
        return True

    if any(
        name.startswith(prefix + ":")
        for prefix in (
            "파일",
            "분류",
            "틀",
            "도움말",
            "위키백과",
            "특수",
            "언어",
        )
    ):
        return True

    if name.startswith(("http://", "https://", "#")):
        return True

    return any(re.search(pattern, name) for pattern in NOISE_PATTERNS)


def extract_link_targets(raw_value):
    """위키 링크의 실제 문서 제목과 표시 문구를 함께 추출한다.

    예:
        [[완득이 (영화)|영화]]
        -> target_title: 완득이 (영화)
           label: 영화

    문서 내부 앵커는 target_title에서 제외한다.
    """
    results = []
    seen = set()

    for match in LINK_WITH_LABEL_PATTERN.finditer(raw_value or ""):
        raw_target = clean_text(match.group(1))
        target_title = clean_text(raw_target.split("#", 1)[0])
        label = clean_text(match.group(2) or target_title)

        if not target_title:
            continue

        key = (target_title, label)
        if key in seen:
            continue

        seen.add(key)
        results.append(
            {
                "target_title": target_title,
                "label": label,
            }
        )

    return results


def extract_link_names(raw_value):
    """기존 후보 추출 호환성을 위해 실제 문서 제목 목록을 반환한다."""
    return [
        item["target_title"]
        for item in extract_link_targets(raw_value)
    ]


def find_link_target_title(raw_value, candidate_name):
    """후보 이름과 일치하는 원본 위키 링크의 실제 문서 제목을 찾는다."""
    candidate_norm = normalize(candidate_name)

    if not candidate_norm:
        return None

    for item in extract_link_targets(raw_value):
        target_title = item["target_title"]
        label = item["label"]

        target_norm = normalize(target_title)
        label_norm = normalize(label)

        # 일반 후보: 문서 제목 또는 표시 문구와 일치
        if candidate_norm in {target_norm, label_norm}:
            return target_title

        # 창작자 후보: '김려령 (소설가)'를 '김려령'으로 정리한 경우
        target_without_role = CREATOR_TITLE_HINT.sub("", target_title).strip()
        if candidate_norm == normalize(target_without_role):
            return target_title

    return None


def extract_title_marked_values(text):
    values = []

    for pattern in (
        r"《([^》]+)》",
        r"〈([^〉]+)〉",
        r"<([^<>]{2,80})>",
    ):
        values.extend(
            clean_text(value)
            for value in re.findall(pattern, text or "")
        )

    return [value for value in values if value]


def extract_creator_names(text):
    creators = []

    # 예: 김만중의 소설, 홍길동전을 원작으로 한 작품 등의 단서
    for match in re.finditer(
        r"([가-힣A-Za-z][가-힣A-Za-z0-9· .]{1,30}?)\s*(?:의\s*)?"
        r"(?:웹툰|만화|소설|웹소설|희곡)",
        text or "",
    ):
        name = clean_text(match.group(1)).strip(" ,·")

        if name and len(name) <= 30 and not is_noise_link(name):
            creators.append(name)

    return list(dict.fromkeys(creators))


def extract_original_work_candidates(raw_value):
    text = infobox_plain_text(raw_value)
    works = extract_link_names(raw_value)
    works.extend(extract_title_marked_values(text))
    creators = extract_creator_names(text)

    return {
        "works": list(
            dict.fromkeys(
                clean_text(value)
                for value in works
                if clean_text(value)
            )
        ),
        "creators": creators,
    }


def extract_plain_infobox_candidates(raw_value, signal_name):
    names = extract_link_names(raw_value)
    plain = infobox_plain_text(raw_value)

    if signal_name in {"genres", "setting", "series"}:
        names.extend(re.split(r"[,，、/·;；]", plain))

    return list(
        dict.fromkeys(
            clean_text(value)
            for value in names
            if clean_text(value)
        )
    )


def extract_infobox_signals(wikitext):
    fields = {}

    for template in top_level_templates(wikitext):
        chunks = split_top_level(template)

        if not chunks or not re.search(
            r"정보상자|인포박스|영화|텔레비전|책|소설|문학|작품",
            chunks[0],
            re.I,
        ):
            continue

        for chunk in chunks[1:]:
            if "=" not in chunk:
                continue

            key, value = chunk.split("=", 1)
            key = clean_text(key).lower()

            for signal_name, aliases in INFOBOX_FIELD_ALIASES.items():
                if key in aliases:
                    fields.setdefault(signal_name, []).append(value.strip())

    signals = {}

    for signal_name, values in fields.items():
        raw = " | ".join(values)
        link_targets = extract_link_targets(raw)

        if signal_name == "original_work":
            parsed = extract_original_work_candidates(raw)

            signals[signal_name] = {
                "text": infobox_plain_text(raw),
                "work_candidates": parsed["works"],
                "creator_candidates": parsed["creators"],
                "link_targets": link_targets,
                "raw_value": raw,
            }

        elif signal_name == "creators":
            creator_names = (
                extract_link_names(raw)
                + extract_creator_names(infobox_plain_text(raw))
            )

            signals[signal_name] = {
                "text": infobox_plain_text(raw),
                "creator_candidates": list(
                    dict.fromkeys(
                        clean_text(value)
                        for value in creator_names
                        if clean_text(value)
                    )
                ),
                "link_targets": link_targets,
                "raw_value": raw,
            }

        else:
            signals[signal_name] = {
                "text": infobox_plain_text(raw),
                "text_candidates": extract_plain_infobox_candidates(
                    raw,
                    signal_name,
                ),
                "link_targets": link_targets,
                "raw_value": raw,
            }

    return signals


def clean_section_title(title):
    return clean_text(re.sub(r"'''?", "", title)).strip("= ")


def classify_section(section_title):
    title = clean_section_title(section_title)

    if not title:
        return None

    if title.lower() in {"도입부", "개요", "서론", "lead"}:
        return {
            "group": "lead",
            "score": 9,
            "max_candidates": 15,
            "max_chars": 700,
        }

    if any(word in title for word in EXCLUDED_SECTION_WORDS):
        return None

    for rule in SECTION_RULES:
        if re.search(rule["pattern"], title, re.I):
            return {
                key: value
                for key, value in rule.items()
                if key != "pattern"
            }

    return None


def split_sections(wikitext):
    matches = list(HEADING_PATTERN.finditer(wikitext or ""))

    sections = [
        {
            "section": "도입부",
            "text": (
                (wikitext or "")[:matches[0].start()]
                if matches
                else (wikitext or "")
            ),
        }
    ]

    for i, match in enumerate(matches):
        end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(wikitext)
        )

        sections.append(
            {
                "section": clean_section_title(match.group(2)),
                "text": wikitext[match.end():end],
            }
        )

    return sections


def remove_templates_and_tables(text):
    text = re.sub(r"(?s)\{\|.*?\|\}", " ", text or "")
    return unwrap_templates(text)


def strip_external_links(text):
    text = re.sub(
        r"\[https?://[^\s\]]+\s+([^\]]+)\]",
        r"\1",
        text or "",
    )
    return re.sub(r"\[https?://[^\s\]]+\]", "", text)


def extract_link_context(section_text, link_name):
    start_marker, end_marker = "\uE000", "\uE001"

    # 후보 이름과 비교할 때는 문서 내부 앵커를 제외한다.
    target_name = clean_text(link_name).split("#", 1)[0]

    def mark(match):
        raw_target = clean_text(match.group(1))
        target = raw_target.split("#", 1)[0]
        shown = clean_text(match.group(2) or target)

        if target == target_name:
            return f"{start_marker}{shown}{end_marker}"

        return shown

    marked = LINK_WITH_LABEL_PATTERN.sub(
        mark,
        strip_external_links(section_text),
    )

    # 목록형 문장에서는 링크가 포함된 항목을 우선 근거로 사용한다.
    for raw_line in marked.splitlines():
        if start_marker in raw_line and re.match(r"^\s*[\*#:]", raw_line):
            excerpt = strip_wikitext(raw_line).strip()
            position = excerpt.find(start_marker)
            local_window = excerpt[
                max(0, position - 15):position + 30
            ]

            clean_excerpt = (
                excerpt.replace(start_marker, "")
                .replace(end_marker, "")
            )

            return clean_excerpt[:400], local_window, False

    plain = strip_wikitext(marked)
    sentences = re.split(r"(?<=[.!?])\s+|\n+", plain)

    sentence = next(
        (
            value.strip()
            for value in sentences
            if start_marker in value
        ),
        plain,
    )

    position = sentence.find(start_marker)

    if position < 0:
        return sentence[:400], "", False

    local_window = sentence[
        max(0, position - 15):position + 30
    ]

    end_position = sentence.find(end_marker, position)
    after = (
        sentence[end_position + len(end_marker):]
        if end_position >= 0
        else ""
    )

    genre_modifier = bool(
        re.match(r"\s*(?:영화|드라마|작품|시리즈)", after)
    )

    excerpt = (
        sentence.replace(start_marker, "")
        .replace(end_marker, "")
    )

    return excerpt[:400], local_window, genre_modifier


def extract_relevant_sections(
    wikitext,
    infobox_signals,
    max_chars=MAX_CONTEXT_CHARS,
):
    selected, total = [], 0

    infobox_lines = [
        f"{key}: {value['text']}"
        for key, value in infobox_signals.items()
        if value.get("text")
    ]

    if infobox_lines:
        text = "\n".join(infobox_lines)[:700]

        selected.append(
            {
                "section": "인포박스",
                "group": "infobox",
                "score": 9,
                "text": text,
            }
        )

        total += len(text)

    for section_data in split_sections(wikitext):
        rule = classify_section(section_data["section"])

        if not rule:
            continue

        plain = strip_wikitext(section_data["text"])

        if len(plain) < 30:
            continue

        remaining = max_chars - total

        if remaining <= 0:
            break

        text = plain[:min(rule["max_chars"], remaining)]

        if len(text) < 30:
            continue

        selected.append(
            {
                "section": section_data["section"],
                "group": rule["group"],
                "score": rule["score"],
                "text": text,
            }
        )

        total += len(text)

    return selected


def mention_reason(section, link_name, local_window, is_genre_modifier=False):
    if section in {"수상", "흥행", "시청률", "참고 사항"}:
        return "metadata_section"

    if re.search(
        r"(?:영화제|어워드|조합상|영화상|예술상|연기상|작품상|종려상)$",
        link_name,
    ):
        return "award_name"

    if (
        not is_genre_modifier
        and any(word in local_window for word in BOX_OFFICE_WORDS)
    ):
        return "boxoffice_near"

    if any(word in local_window for word in FUTURE_PROJECT_WORDS):
        return "future_project_near"

    return None


def related_work_hint(local_window):
    for hint, words in RELATED_WORK_WORDS.items():
        if any(word in local_window for word in words):
            return hint

    return None


def extract_section_candidates(item, wikitext, infobox_signals):
    candidates_by_key = {}

    def add(candidate):
        key = (
            candidate["relation_type"],
            candidate["entity_type"],
            normalize(candidate["name"]),
        )

        observation = {
            key_name: candidate.get(key_name)
            for key_name in (
                "section",
                "group",
                "score",
                "excerpt",
                "mention_reason",
                "relation_hint",
                "link_target_title",
            )
        }

        previous = candidates_by_key.get(key)

        if previous is None:
            candidate["observations"] = [observation]
            candidates_by_key[key] = candidate
            return

        known = {
            (
                observation_item.get("section"),
                observation_item.get("excerpt"),
                observation_item.get("link_target_title"),
            )
            for observation_item in previous.get("observations", [])
        }

        observation_key = (
            observation["section"],
            observation["excerpt"],
            observation["link_target_title"],
        )

        if observation_key not in known:
            previous.setdefault("observations", []).append(observation)

        # 동일 후보의 다른 문맥에서 링크 대상이 확인되면 보존한다.
        if (
            not previous.get("link_target_title")
            and candidate.get("link_target_title")
        ):
            previous["link_target_title"] = candidate["link_target_title"]

        if candidate["score"] > previous["score"]:
            observations = previous["observations"]

            # 기존 후보의 링크 대상 정보도 새 대표 후보에 유지한다.
            if (
                not candidate.get("link_target_title")
                and previous.get("link_target_title")
            ):
                candidate["link_target_title"] = (
                    previous["link_target_title"]
                )

            candidate["observations"] = observations
            candidates_by_key[key] = candidate

    original = infobox_signals.get("original_work", {})
    original_raw = original.get("raw_value", "")

    for name in original.get("work_candidates", []):
        name = clean_text(name)

        if name and not is_noise_link(name):
            add(
                {
                    "name": name,
                    "section": "인포박스",
                    "group": "original_work",
                    "score": 9,
                    "excerpt": (
                        f"인포박스 original_work: "
                        f"{original.get('text', '')}"
                    )[:400],
                    "relation_type": "wikipedia_original_candidate",
                    "entity_type": "source_work",
                    "link_target_title": find_link_target_title(
                        original_raw,
                        name,
                    ),
                }
            )

    creator_signals = list(original.get("creator_candidates", []))
    creator_signals.extend(
        infobox_signals.get("creators", {}).get(
            "creator_candidates",
            [],
        )
    )

    creator_raw_values = [
        original_raw,
        infobox_signals.get("creators", {}).get("raw_value", ""),
    ]

    for name in dict.fromkeys(creator_signals):
        name = clean_text(name)

        if name and not is_noise_link(name):
            link_target_title = None

            for raw_value in creator_raw_values:
                link_target_title = find_link_target_title(
                    raw_value,
                    name,
                )

                if link_target_title:
                    break

            add(
                {
                    "name": name,
                    "section": "인포박스",
                    "group": "original_creator",
                    "score": 8,
                    "excerpt": (
                        f"인포박스 original_work: "
                        f"{original.get('text', '')}"
                    )[:400],
                    "relation_type": "wikipedia_creator_candidate",
                    "entity_type": "person",
                    "link_target_title": link_target_title,
                }
            )

    series_signal = infobox_signals.get("series", {})
    series_raw = series_signal.get("raw_value", "")

    for name in series_signal.get("text_candidates", []):
        name = clean_text(name)

        if name and not is_noise_link(name):
            add(
                {
                    "name": name,
                    "section": "인포박스",
                    "group": "series",
                    "score": 9,
                    "excerpt": (
                        f"인포박스 series: "
                        f"{series_signal.get('text', '')}"
                    )[:400],
                    "relation_type": "wikipedia_series_candidate",
                    "entity_type": "source_work",
                    "link_target_title": find_link_target_title(
                        series_raw,
                        name,
                    ),
                }
            )

    for signal_name in ("genres", "setting"):
        signal = infobox_signals.get(signal_name)

        if not signal:
            continue

        for name in signal.get("text_candidates", []):
            name = clean_text(name)

            if name and not is_noise_link(name):
                add(
                    {
                        "name": name,
                        "section": "인포박스",
                        "group": signal_name,
                        "score": 7 if signal_name == "genres" else 8,
                        "excerpt": (
                            f"인포박스 {signal_name}: "
                            f"{signal.get('text', '')}"
                        )[:400],
                        "relation_type": "wikipedia_context_candidate",
                        "entity_type": "concept",
                        "link_target_title": find_link_target_title(
                            signal.get("raw_value", ""),
                            name,
                        ),
                    }
                )

    for section_data in split_sections(wikitext):
        section = section_data["section"]
        section_text = section_data["text"]
        rule = classify_section(section)

        if not rule:
            continue

        cleaned = remove_templates_and_tables(section_text)
        seen, count = set(), 0

        for match in LINK_PATTERN.finditer(cleaned):
            # 실제 위키 문서 제목을 보존한다.
            link_target_title = clean_text(
                match.group(1).split("#", 1)[0]
            )
            link_name = link_target_title
            norm = normalize(link_name)

            if (
                not norm
                or norm in seen
                or is_noise_link(link_name)
                or norm == normalize(item["title"])
            ):
                continue

            if count >= rule["max_candidates"]:
                break

            seen.add(norm)
            count += 1

            excerpt, local_window, genre_modifier = extract_link_context(
                cleaned,
                link_name,
            )

            reason = mention_reason(
                section,
                link_name,
                local_window,
                genre_modifier,
            )

            is_work = bool(WORK_TITLE_HINT.search(link_name))
            is_creator = bool(CREATOR_TITLE_HINT.search(link_name))

            hint = related_work_hint(local_window) if is_work else None

            if is_creator:
                rel = "wikipedia_creator_candidate"
                etype = "person"
                group = "creator"
                score = max(rule["score"], 8)

                candidate_name = re.sub(
                    r"\s*\([^)]*\)$",
                    "",
                    link_name,
                )
                reason = None

            elif is_work and hint:
                rel = "wikipedia_related_work_candidate"
                etype = "source_work"
                group = "related_work"
                score = max(rule["score"], 6)
                candidate_name = link_name
                reason = None

            elif reason:
                if is_work:
                    rel = "wikipedia_related_work_candidate"
                    etype = "source_work"
                    group = "related_work"
                    score = min(rule["score"], 2)
                    hint = hint or "comparison"
                    reason = "comparison_work"
                else:
                    rel = "wikipedia_mention_candidate"
                    etype = "concept"
                    group = "mention"
                    score = min(rule["score"], 2)

                candidate_name = link_name

            elif is_work:
                rel = "wikipedia_related_work_candidate"
                etype = "source_work"
                group = "related_work"
                score = max(rule["score"], 6)
                candidate_name = link_name

            else:
                rel = "wikipedia_context_candidate"
                etype = "concept"
                group = rule["group"]
                score = rule["score"]
                candidate_name = link_name

            add(
                {
                    "name": candidate_name,
                    "section": section,
                    "group": group,
                    "score": score,
                    "excerpt": excerpt,
                    "mention_reason": reason,
                    "relation_hint": hint,
                    "relation_type": rel,
                    "entity_type": etype,
                    "link_target_title": link_target_title,
                }
            )

    candidates = score_candidates(
        list(candidates_by_key.values()),
        link_counts(wikitext, LINK_PATTERN, normalize),
        normalize,
    )

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate["score"],
            candidate["relation_type"],
            candidate["name"],
        ),
    )