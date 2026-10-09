import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv

from common import normalize_name, require_normalized_name
from candidate_scoring import SCORE_VERSION, link_counts, score_candidates


# =========================================================
# 1. 경로 및 기본 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]

SELECTION_PATH = (
    ROOT / "data" / "selections" / "selected_movie_drama_contexts.jsonl"
)
CACHE_DIR = ROOT / "data" / "enrichment" / "wikipedia_refined"

WIKIPEDIA_API = "https://ko.wikipedia.org/w/api.php"

load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]

WIKIMEDIA_CONTACT = os.getenv(
    "WIKIMEDIA_CONTACT",
    "mailto:content-exploration@example.com",
)

USER_AGENT = (
    f"content-exploration-capstone/1.0 ({WIKIMEDIA_CONTACT})"
)

MAX_CANDIDATES_PER_CONTENT = 100
MAX_CONTEXT_CHARS = 3_000
REQUEST_DELAY_SECONDS = 1.0


# =========================================================
# 2. 위키백과 문서명 수동 지정
# =========================================================

PAGE_OVERRIDES = {
    ("movie", 496243): "기생충 (영화)",
    ("movie", 11423): "살인의 추억",
    ("movie", 282631): "명량 (영화)",
    ("movie", 437103): "1987 (2017년 영화)",
    ("movie", 242452): "변호인 (영화)",
    ("movie", 382336): "동주 (영화)",
    ("movie", 290098): "아가씨 (영화)",
    ("movie", 397567): "신과함께-죄와 벌",
    ("movie", 396535): "부산행",
    ("movie", 313108): "국제시장 (영화)",
    ("drama", 75820): "미스터 션샤인",
    ("drama", 70593): "킹덤 (2019년 드라마)",
    ("drama", 132925): "옷소매 붉은 끝동",
    ("drama", 64840): "시그널 (드라마)",
    ("drama", 61678): "미생 (드라마)",
    ("drama", 110534): "D.P.",
    ("drama", 197067): "이상한 변호사 우영우",
    ("drama", 126485): "무빙 (드라마)",
    ("drama", 214528): "정년이 (드라마)",
    ("drama", 99494): "악의 꽃 (2020년 드라마)",
}


# =========================================================
# 3. 추출할 문서 섹션
# =========================================================

SECTION_RULES = [
    {
        "names": ["줄거리", "시놉시스", "내용", "스토리", "개요"],
        "group": "narrative",
        "score": 10,
        "max_candidates": 15,
        "max_chars": 1200,
    },
    {
        "names": [
            "배경", "역사적 배경", "시대적 배경", "사회적 배경",
            "주제", "해석", "의미", "모티프", "고증",
        ],
        "group": "context",
        "score": 8,
        "max_candidates": 15,
        "max_chars": 900,
    },
    {
        "names": ["원작", "각색", "원작과의 차이", "제작 배경", "스핀오프"],
        "group": "adaptation",
        "score": 9,
        "max_candidates": 12,
        "max_chars": 800,
    },
    {
        "names": ["같이 보기", "관련 작품", "관련 문서"],
        "group": "related",
        "score": 4,
        "max_candidates": 10,
        "max_chars": 500,
    },
]

EXCLUDED_SECTION_WORDS = {
    "출연", "캐스팅", "등장인물", "등장 인물", "제작진", "음악",
    "사운드트랙", "수상", "흥행", "평가", "평론", "시청률",
    "방영", "갤러리", "에피소드", "논란", "각주", "주석",
    "참고", "외부 링크", "참고 문헌", "분류", "전거 통제",
}


# =========================================================
# 4. 링크 필터 및 추출 규칙
# =========================================================

NOISE_PATTERNS = [
    r"^\d{4}년?$",
    r"^\d{1,2}월$",
    r"^\d{1,2}월 \d{1,2}일$",
    r"^\d{4}년 \d{1,2}월 \d{1,2}일$",
    r"^20\d{2}년 대한민국의 텔레비전 드라마 목록$",
    r"^대한민국의 텔레비전 드라마 목록.*$",
    r"^.* (금토|수목|월화|토일) 드라마$",
    r"^(KBS|MBC|SBS|JTBC|tvN|ENA|TV조선|넷플릭스|디즈니\+|KNN)$",
    r"^.*아카데미.*상$",
    r"^제\d+회 아카데미상$",
    r"^.*골든 글로브.*$",
    r"^.*칸 국제 영화제.*$",
    r"^.*칸 영화제.*$",
    r"^.*백상예술대상.*$",
    r"^.*청룡영화상.*$",
    r"^.*대종상.*$",
    r"^.*부일영화상.*$",
    r"^.*영화평론가협회상.*$",
    r"^.*영화상$",
    r"^.*어워드$",
    r"^.*시상식$",
    r"^.*뉴스$",
    r"^.*신문$",
    r"^.*종려상$",
    r"^.*엔터테인먼트$",
]

GENERIC_LINKS = {
    "대한민국",
    "한국",
    "한국어",
    "영화",
    "드라마",
    "장편 영화",
    "대한민국의 영화 흥행 기록",
    "천만 관객 돌파 영화",
    "대한민국의 텔레비전 드라마 목록",
    "웹툰을 원작으로 삼은 드라마 목록",
    "한국 표준시",
    "다음",
}

INFOBOX_FIELD_ALIASES = {
    "original_work": {"원작", "원작품"},
    "genres": {"장르"},
    "setting": {"배경", "시대", "시대적 배경"},
    "series": {"시리즈", "스핀오프", "후속작"},
}

REFINED_RELATION_TYPES = [
    "wikipedia_context_candidate",
    "wikipedia_original_candidate",
    "wikipedia_series_candidate",
    "wikipedia_related_work_candidate",
    "wikipedia_creator_candidate",
    "wikipedia_mention_candidate",
]

MENTION_SENTENCE_PATTERNS = [
    r"관객",
    r"흥행",
    r"돌파",
    r"수상",
    r"시상",
    r"초청",
    r"배급",
    r"차기작",
    r"소문",
    r"예상",
    r"발표",
    r"주연.*된다는",
]

BOX_OFFICE_WORDS = ("관객", "흥행", "돌파", "흥행수익")

FUTURE_PROJECT_WORDS = (
    "차기작",
    "차기 작품",
    "소문",
    "예상",
    "발표",
    "개발 단계",
)

WORK_TITLE_HINT = re.compile(
    r"\((?:\d{4}년\s+)?(?:영화|드라마|만화|웹툰|소설)\)$"
)

CREATOR_TITLE_HINT = re.compile(
    r"\((?:영화 감독|감독|연출가|작가|소설가|만화가|웹툰 작가)\)$"
)

RELATED_WORK_WORDS = {
    "prequel": ("프리퀄", "프리퀼"),
    "sequel": ("속편", "후속작"),
    "spin_off": ("스핀오프",),
    "remake": ("리메이크",),
    "adaptation": ("원작", "각색", "실사화"),
}

# 위키백과 문서의 == 제목 == 형식을 추출한다.
HEADING_PATTERN = re.compile(
    r"(?m)^(={2,6})\s*(.*?)\s*\1\s*$"
)

# 링크 대상과 표시 문구를 추출한다.
# 예: [[기생충 (영화)|기생충]] -> 대상: 기생충 (영화)
LINK_PATTERN = re.compile(
    r"\[\[([^|#\]]+)(?:#[^|\]]*)?(?:\|[^\]]*)?\]\]"
)

LINK_WITH_LABEL_PATTERN = re.compile(
    r"\[\[([^|#\]]+)(?:#[^|\]]*)?(?:\|([^\]]*))?\]\]"
)


# =========================================================
# 5. 기본 문자열 처리
# =========================================================

def clean_text(value):
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def normalize(value):
    """후보 비교용 정규화. DB 저장값 자체를 바꾸지는 않는다."""
    value = clean_text(value)

    if not value:
        return ""

    return normalize_name(value)


def request_json(params):
    request = Request(
        f"{WIKIPEDIA_API}?{urlencode(params)}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def page_url(page_title):
    encoded_title = quote(page_title.replace(" ", "_"))
    return f"https://ko.wikipedia.org/wiki/{encoded_title}"


# =========================================================
# 6. 위키 문법 정리
# =========================================================

def strip_wikitext(text):
    if not text:
        return ""

    # 표 제거
    text = re.sub(r"\{\|.*?\|\}", " ", text, flags=re.DOTALL)

    # 참조 태그 제거
    text = re.sub(
        r"<ref\b[^>/]*>.*?</ref>",
        " ",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r"<ref\b[^>]*/\s*>",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    # 중첩되지 않은 템플릿을 반복 제거
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text, flags=re.DOTALL)

    # [[대상|표시 문구]] -> 표시 문구
    text = re.sub(
        r"\[\[([^|\]]+)\|([^\]]+)\]\]",
        r"\2",
        text,
    )

    # [[대상]] -> 대상
    text = re.sub(
        r"\[\[([^\]]+)\]\]",
        r"\1",
        text,
    )

    # 위키 강조 표시 제거
    text = re.sub(r"'{2,}", "", text)

    # HTML 태그 제거
    text = re.sub(r"<[^>]+>", " ", text)

    # 공백 정리
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def split_top_level(text, separator="|"):
    """중첩된 템플릿과 링크 내부의 구분자는 무시하고 분리한다."""
    parts = []
    buffer = []

    template_depth = 0
    link_depth = 0
    index = 0

    while index < len(text):
        pair = text[index:index + 2]

        if pair == "{{":
            template_depth += 1
            buffer.append(pair)
            index += 2
            continue

        if pair == "}}":
            template_depth = max(0, template_depth - 1)
            buffer.append(pair)
            index += 2
            continue

        if pair == "[[":
            link_depth += 1
            buffer.append(pair)
            index += 2
            continue

        if pair == "]]":
            link_depth = max(0, link_depth - 1)
            buffer.append(pair)
            index += 2
            continue

        if (
            text[index] == separator
            and template_depth == 0
            and link_depth == 0
        ):
            parts.append("".join(buffer))
            buffer = []
            index += 1
            continue

        buffer.append(text[index])
        index += 1

    parts.append("".join(buffer))
    return parts


def top_level_templates(wikitext):
    """문서에서 최상위 템플릿을 추출한다."""
    templates = []
    index = 0

    while index < len(wikitext) - 1:
        if wikitext[index:index + 2] != "{{":
            index += 1
            continue

        start = index
        depth = 0

        while index < len(wikitext) - 1:
            pair = wikitext[index:index + 2]

            if pair == "{{":
                depth += 1
                index += 2
                continue

            if pair == "}}":
                depth -= 1
                index += 2

                if depth == 0:
                    templates.append(wikitext[start + 2:index - 2])
                    break

                continue

            index += 1

        else:
            break

    return templates


def unwrap_templates(text):
    """템플릿 내부의 매개변수 값을 최대한 일반 텍스트로 변환한다."""
    if not text:
        return ""

    previous = None

    while previous != text:
        previous = text

        def replace_template(match):
            parts = split_top_level(match.group(1))

            # {{템플릿명}}처럼 매개변수가 없으면 제거
            if len(parts) <= 1:
                return " "

            values = []

            for part in parts[1:]:
                if "=" in part:
                    _, value = part.split("=", 1)
                    values.append(value)
                else:
                    values.append(part)

            return " ".join(values)

        text = re.sub(
            r"\{\{([^{}]*)\}\}",
            replace_template,
            text,
            flags=re.DOTALL,
        )

    return text


def infobox_plain_text(raw_value):
    if not raw_value:
        return ""

    raw_value = re.sub(
        r"<br\s*/?>",
        ",",
        raw_value,
        flags=re.IGNORECASE,
    )

    # 인포박스에서 꺾쇠로 감싼 링크/제목을 보존한다.
    raw_value = re.sub(
        r"<\s*(\[\[[^\]]+\]\])\s*>",
        r"《\1》",
        raw_value,
    )

    raw_value = re.sub(
        r"<([가-힣A-Za-z0-9 .!?:'’\-]{2,100})>",
        r"《\1》",
        raw_value,
    )

    return strip_wikitext(unwrap_templates(raw_value))


# =========================================================
# 7. 링크 필터링 및 인포박스 후보 추출
# =========================================================

def is_noise_link(name):
    name = clean_text(name)

    if not name:
        return True

    # 위키백과의 분류, 파일, 도움말 등 네임스페이스 링크 제외
    if ":" in name:
        return True

    if name in GENERIC_LINKS:
        return True

    compact_name = re.sub(r"\s+", "", name)

    if compact_name in {
        "KBS",
        "MBC",
        "SBS",
        "JTBC",
        "tvN",
        "ENA",
        "TV조선",
        "넷플릭스",
        "디즈니+",
        "디즈니플러스",
        "KNN",
        "HBO",
        "대한민국영화",
        "대한민국의영화",
        "대한민국드라마",
        "대한민국의드라마",
    }:
        return True

    return any(
        re.match(pattern, name, flags=re.IGNORECASE)
        for pattern in NOISE_PATTERNS
    )


def extract_link_names(raw_value):
    names = []

    for match in LINK_PATTERN.finditer(raw_value or ""):
        name = clean_text(match.group(1))

        if name and not is_noise_link(name):
            names.append(name)

    return list(dict.fromkeys(names))


def extract_title_marked_values(text):
    """《작품명》, 〈작품명〉, <작품명> 형태의 제목을 추출한다."""
    matches = re.findall(
        r"[《〈<]([^》〉>]{2,100})[》〉>]",
        text or "",
    )

    return list(
        dict.fromkeys(
            title
            for title in map(clean_text, matches)
            if title and not is_noise_link(title)
        )
    )


def extract_creator_names(text):
    if not text:
        return []

    media_match = re.search(
        r"(?:웹툰|만화|소설|웹소설|희곡)",
        text,
    )

    if not media_match:
        return []

    prefix = text[:media_match.start()]

    prefix = re.sub(
        r"(글|그림|원작|저자|작가)\s*",
        "",
        prefix,
    )

    prefix = re.sub(r"의\s*$", "", prefix).strip()

    result = []

    for part in re.split(
        r"[,/·ㆍ]|\s+(?:과|와|및)\s+",
        prefix,
    ):
        part = clean_text(part)

        if not part:
            continue

        part = re.sub(r"\([^)]*\)", "", part).strip()

        if (
            re.fullmatch(
                r"[가-힣]{2,8}(?:\s+[가-힣]{2,12})*",
                part,
            )
            and not is_noise_link(part)
        ):
            result.append(part)

    seen = set()
    unique_result = []

    for name in result:
        key = normalize(name)

        if key and key not in seen:
            seen.add(key)
            unique_result.append(name)

    return unique_result


def extract_original_work_candidates(raw_value):
    raw_value = clean_text(raw_value)

    if not raw_value:
        return {"works": [], "creators": []}

    plain_text = infobox_plain_text(raw_value)

    creators = extract_creator_names(plain_text)
    works = extract_title_marked_values(plain_text)

    if not works:
        media_match = re.search(
            r"(?:웹툰|만화|소설|웹소설|희곡)",
            plain_text,
        )

        if media_match:
            tail = plain_text[media_match.end():].strip()

            tail = re.split(
                r"(?:을|를)\s*(?:원작|바탕)|"
                r"(?:을|를)\s*각색|원작으로",
                tail,
                maxsplit=1,
            )[0]

            tail = re.sub(r"\([^)]*\)", "", tail)
            tail = tail.strip().strip(" :：-–—.,")

            if (
                tail
                and 2 <= len(tail) <= 100
                and not is_noise_link(tail)
            ):
                works.append(tail)

    creator_keys = {normalize(creator) for creator in creators}

    result_works = []
    seen_works = set()

    for work in works:
        work = clean_text(work)
        key = normalize(work)

        if (
            not key
            or key in seen_works
            or key in creator_keys
            or is_noise_link(work)
        ):
            continue

        seen_works.add(key)
        result_works.append(work)

    return {
        "works": result_works,
        "creators": creators,
    }


def extract_plain_infobox_candidates(raw_value, signal_name):
    raw_value = clean_text(raw_value)

    if not raw_value:
        return []

    plain_text = infobox_plain_text(raw_value)
    candidates = []

    if signal_name == "genres":
        for value in re.split(r"[,/·ㆍ]", plain_text):
            value = clean_text(value)

            if value and not is_noise_link(value):
                candidates.append(value)

    elif signal_name == "setting":
        setting = re.sub(
            r"^(배경|시대|시대적 배경)\s*[:：]?\s*",
            "",
            plain_text,
        ).strip()

        if (
            setting
            and len(setting) <= 100
            and not is_noise_link(setting)
        ):
            candidates.append(setting)

    elif signal_name == "series":
        candidates.extend(extract_title_marked_values(plain_text))
        candidates.extend(extract_link_names(raw_value))

    seen = set()
    result = []

    for candidate in candidates:
        key = normalize(candidate)

        if key and key not in seen:
            seen.add(key)
            result.append(candidate)

    return result


def extract_infobox_signals(wikitext):
    for template in top_level_templates(wikitext):
        parts = split_top_level(template)

        if not parts:
            continue

        template_name = strip_wikitext(parts[0])
        fields = {}

        for part in parts[1:]:
            if "=" not in part:
                continue

            key, value = part.split("=", 1)

            key = clean_text(strip_wikitext(key))
            value = clean_text(value)

            if key and value:
                fields[key] = value

        normalized_keys = {normalize(key) for key in fields}

        wanted_keys = {
            normalize(alias)
            for aliases in INFOBOX_FIELD_ALIASES.values()
            for alias in aliases
        }

        if not (
            "정보" in template_name
            or normalized_keys & wanted_keys
        ):
            continue

        signals = {}

        for signal_name, aliases in INFOBOX_FIELD_ALIASES.items():
            alias_keys = {normalize(alias) for alias in aliases}

            for field_name, raw_value in fields.items():
                if normalize(field_name) not in alias_keys:
                    continue

                plain_text = infobox_plain_text(raw_value)

                if signal_name == "original_work":
                    original_data = extract_original_work_candidates(
                        raw_value
                    )

                    signals[signal_name] = {
                        "text": plain_text[:500],
                        "work_candidates": original_data["works"],
                        "creator_candidates": original_data["creators"],
                    }

                else:
                    signals[signal_name] = {
                        "text": plain_text[:500],
                        "text_candidates": extract_plain_infobox_candidates(
                            raw_value,
                            signal_name,
                        ),
                    }

                break

        if signals:
            return signals

    return {}


# =========================================================
# 8. 섹션 추출
# =========================================================

def clean_section_title(title):
    return strip_wikitext(title).strip()


def classify_section(section_title):
    if section_title == "lead":
        return {
            "group": "lead",
            "score": 9,
            "max_candidates": 15,
            "max_chars": 700,
        }

    if any(
        word in section_title
        for word in EXCLUDED_SECTION_WORDS
    ):
        return None

    for rule in SECTION_RULES:
        if any(
            name in section_title
            for name in rule["names"]
        ):
            return rule

    return None


def split_sections(wikitext):
    headings = list(HEADING_PATTERN.finditer(wikitext))
    sections = []

    lead_end = headings[0].start() if headings else len(wikitext)
    lead_text = wikitext[:lead_end].strip()

    if lead_text:
        sections.append({
            "section": "lead",
            "text": lead_text,
        })

    for index, heading in enumerate(headings):
        section_title = clean_section_title(heading.group(2))
        content_start = heading.end()

        content_end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(wikitext)
        )

        section_text = wikitext[content_start:content_end].strip()

        if section_title and section_text:
            sections.append({
                "section": section_title,
                "text": section_text,
            })

    return sections


def remove_templates_and_tables(text):
    text = re.sub(r"\{\|.*?\|\}", " ", text, flags=re.DOTALL)

    text = re.sub(
        r"<ref\b[^>/]*>.*?</ref>",
        " ",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    text = re.sub(
        r"<ref\b[^>]*/\s*>",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    previous = None

    while previous != text:
        previous = text
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text, flags=re.DOTALL)

    return text


def strip_external_links(text):
    # [https://example.com 표시 문구] -> 표시 문구
    text = re.sub(
        r"\[https?://[^\s\]]+\s+([^\]]+)\]",
        r"\1",
        text,
    )

    # 표시 문구 없는 외부 링크 제거
    return re.sub(
        r"\[https?://[^\s\]]+\]",
        "",
        text,
    )


def extract_link_context(section_text, link_name):
    marker_start = "\uE000"
    marker_end = "\uE001"

    def replace_link(match):
        target = clean_text(match.group(1)) or ""
        shown = clean_text(match.group(2)) or target

        if target == link_name:
            return f"{marker_start}{shown}{marker_end}"

        return shown

    marked = LINK_WITH_LABEL_PATTERN.sub(
        replace_link,
        strip_external_links(section_text),
    )

    # 목록형 문장에 링크가 있을 경우 우선 추출
    for raw_line in marked.splitlines():
        if (
            marker_start in raw_line
            and re.match(r"^\s*[*#:]", raw_line)
        ):
            excerpt = strip_wikitext(raw_line).strip()
            position = excerpt.find(marker_start)

            local_window = excerpt[
                max(0, position - 15):position + 30
            ]

            clean_excerpt = (
                excerpt.replace(marker_start, "")
                .replace(marker_end, "")
            )

            return clean_excerpt[:400], local_window, False

    plain = strip_wikitext(marked).strip()

    sentences = re.split(
        r"(?<=[.!?])\s+|\n+",
        plain,
    )

    sentence = next(
        (
            value.strip()
            for value in sentences
            if marker_start in value
        ),
        plain,
    )

    position = sentence.find(marker_start)

    if position < 0:
        return sentence[:400], "", False

    local_window = sentence[
        max(0, position - 15):position + 30
    ]

    marker_end_position = sentence.find(marker_end, position)

    if marker_end_position >= 0:
        after_marker = sentence[
            marker_end_position + len(marker_end):
        ]
    else:
        after_marker = ""

    is_genre_modifier = bool(
        re.match(
            r"\s*(?:영화|드라마|작품|시리즈)",
            after_marker,
        )
    )

    clean_sentence = (
        sentence.replace(marker_start, "")
        .replace(marker_end, "")
    )

    return clean_sentence[:400], local_window, is_genre_modifier


def extract_relevant_sections(wikitext, infobox_signals):
    selected_sections = []
    total_chars = 0
    infobox_lines = []

    for signal_name, value in infobox_signals.items():
        if value.get("text"):
            infobox_lines.append(
                f"{signal_name}: {value['text']}"
            )

    if infobox_lines:
        infobox_text = "\n".join(infobox_lines)[:700]

        selected_sections.append({
            "section": "인포박스",
            "group": "infobox",
            "score": 9,
            "text": infobox_text,
        })

        total_chars += len(infobox_text)

    for section_data in split_sections(wikitext):
        section = section_data["section"]
        rule = classify_section(section)

        if rule is None:
            continue

        plain_text = strip_wikitext(section_data["text"])

        if len(plain_text) < 30:
            continue

        remaining = MAX_CONTEXT_CHARS - total_chars

        if remaining <= 0:
            break

        text = plain_text[:rule["max_chars"]][:remaining]

        if len(text) < 30:
            continue

        selected_sections.append({
            "section": section,
            "group": rule["group"],
            "score": rule["score"],
            "text": text,
        })

        total_chars += len(text)

    return selected_sections


# =========================================================
# 9. 후보 관계 생성
# =========================================================

def extract_section_candidates(item, wikitext, infobox_signals):
    candidates_by_key = {}

    def add_candidate(candidate):
        key = (
            f"{candidate['relation_type']}:"
            f"{candidate['entity_type']}:"
            f"{normalize(candidate['name'])}"
        )

        previous = candidates_by_key.get(key)

        observation = {
            "section": candidate["section"],
            "group": candidate["group"],
            "score": candidate["score"],
            "excerpt": candidate["excerpt"],
            "mention_reason": candidate.get("mention_reason"),
            "relation_hint": candidate.get("relation_hint"),
        }

        if previous is None:
            candidate["observations"] = [observation]
            candidates_by_key[key] = candidate
            return

        observation_key = (
            observation["section"],
            observation["excerpt"],
        )

        known = {
            (obs["section"], obs["excerpt"])
            for obs in previous.get("observations", [])
        }

        if observation_key not in known:
            previous.setdefault("observations", []).append(observation)

        if candidate["score"] > previous["score"]:
            candidate["observations"] = previous["observations"]
            candidates_by_key[key] = candidate

    # -----------------------------------------------------
    # 9-1. 인포박스 원작 및 창작자 후보
    # -----------------------------------------------------

    original_signal = infobox_signals.get("original_work")

    if original_signal:
        for name in original_signal.get("work_candidates", []):
            name = clean_text(name)

            if name and not is_noise_link(name):
                add_candidate({
                    "name": name,
                    "section": "인포박스",
                    "group": "original_work",
                    "score": 9,
                    "excerpt": (
                        f"인포박스 original_work: "
                        f"{original_signal.get('text', '')}"
                    )[:400],
                    "relation_type": "wikipedia_original_candidate",
                    "entity_type": "source_work",
                })

        for name in original_signal.get("creator_candidates", []):
            name = clean_text(name)

            if name and not is_noise_link(name):
                add_candidate({
                    "name": name,
                    "section": "인포박스",
                    "group": "original_creator",
                    "score": 8,
                    "excerpt": (
                        f"인포박스 original_work: "
                        f"{original_signal.get('text', '')}"
                    )[:400],
                    "relation_type": "wikipedia_creator_candidate",
                    "entity_type": "person",
                })

    # -----------------------------------------------------
    # 9-2. 인포박스 시리즈 후보
    # -----------------------------------------------------

    series_signal = infobox_signals.get("series")

    if series_signal:
        for name in series_signal.get("text_candidates", []):
            name = clean_text(name)

            if name and not is_noise_link(name):
                add_candidate({
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
                })

    # -----------------------------------------------------
    # 9-3. 인포박스 장르 및 배경 후보
    # -----------------------------------------------------

    for signal_name in ("genres", "setting"):
        signal = infobox_signals.get(signal_name)

        if not signal:
            continue

        score = 7 if signal_name == "genres" else 8

        for name in signal.get("text_candidates", []):
            name = clean_text(name)

            if name and not is_noise_link(name):
                add_candidate({
                    "name": name,
                    "section": "인포박스",
                    "group": signal_name,
                    "score": score,
                    "excerpt": (
                        f"인포박스 {signal_name}: "
                        f"{signal.get('text', '')}"
                    )[:400],
                    "relation_type": "wikipedia_context_candidate",
                    "entity_type": "concept",
                })

    # -----------------------------------------------------
    # 9-4. 본문 섹션의 링크 후보
    # -----------------------------------------------------

    for section_data in split_sections(wikitext):
        section = section_data["section"]
        section_text = section_data["text"]

        rule = classify_section(section)

        if rule is None:
            continue

        cleaned_text = remove_templates_and_tables(section_text)

        section_count = 0
        section_seen = set()

        for match in LINK_PATTERN.finditer(cleaned_text):
            link_name = clean_text(match.group(1))
            normalized_name = normalize(link_name)

            if (
                not normalized_name
                or normalized_name in section_seen
                or is_noise_link(link_name)
            ):
                continue

            if normalized_name == normalize(item["title"]):
                continue

            if section_count >= rule["max_candidates"]:
                break

            section_seen.add(normalized_name)
            section_count += 1

            excerpt, local_window, is_genre_modifier = (
                extract_link_context(cleaned_text, link_name)
            )

            reason = mention_reason(
                section,
                link_name,
                local_window,
                is_genre_modifier=is_genre_modifier,
            )

            is_work = WORK_TITLE_HINT.search(link_name) is not None
            is_creator = CREATOR_TITLE_HINT.search(link_name) is not None

            relation_hint = (
                related_work_hint(local_window)
                if is_work
                else None
            )

            if is_creator:
                relation_type = "wikipedia_creator_candidate"
                entity_type = "person"
                group = "creator"
                score = max(rule["score"], 8)

                candidate_name = re.sub(
                    r"\s*\([^)]*\)$",
                    "",
                    link_name,
                )

                reason = None

            elif is_work and relation_hint:
                relation_type = "wikipedia_related_work_candidate"
                entity_type = "source_work"
                group = "related_work"
                score = max(rule["score"], 6)

                reason = None
                candidate_name = link_name

            elif reason:
                if is_work:
                    relation_type = "wikipedia_related_work_candidate"
                    entity_type = "source_work"
                    group = "related_work"
                    score = min(rule["score"], 2)

                    relation_hint = relation_hint or "comparison"
                    reason = "comparison_work"

                else:
                    relation_type = "wikipedia_mention_candidate"
                    entity_type = "concept"
                    group = "mention"
                    score = min(rule["score"], 2)

                candidate_name = link_name

            elif is_work:
                relation_type = "wikipedia_related_work_candidate"
                entity_type = "source_work"
                group = "related_work"
                score = max(rule["score"], 6)

                candidate_name = link_name

            else:
                relation_type = "wikipedia_context_candidate"
                entity_type = "concept"
                group = rule["group"]
                score = rule["score"]

                candidate_name = link_name

            add_candidate({
                "name": candidate_name,
                "section": section,
                "group": group,
                "score": score,
                "excerpt": excerpt,
                "mention_reason": reason,
                "relation_hint": relation_hint,
                "relation_type": relation_type,
                "entity_type": entity_type,
            })

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


def mention_reason(
    section,
    link_name,
    local_window,
    is_genre_modifier=False,
):
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


# =========================================================
# 10. DB 연결 및 관계 저장
# =========================================================

def db_content_type(item_content_type):
    return "tv" if item_content_type == "drama" else item_content_type


def get_content_id(conn, item):
    row = conn.execute(
        """
        SELECT c.content_id
        FROM content AS c
        JOIN content_source_map AS sm
          ON sm.content_id = c.content_id
        WHERE sm.source_name = 'tmdb'
          AND sm.source_id = %s
          AND c.content_type = %s
        ORDER BY c.content_id
        LIMIT 1
        """,
        (
            str(item["source_ids"]["tmdb"]),
            db_content_type(item["content_type"]),
        ),
    ).fetchone()

    return row["content_id"] if row else None


def get_verified_entity_names(conn, content_id):
    rows = conn.execute(
        """
        SELECT e.entity_name
        FROM relationship AS r
        JOIN entity AS e
          ON r.target_type = 'entity'
         AND e.entity_id = r.target_id
        WHERE r.source_type = 'content'
          AND r.source_id = %s
          AND r.target_type = 'entity'
          AND r.fact_status = 'verified'
          AND r.visibility = 'shown'
        """,
        (content_id,),
    ).fetchall()

    return {
        normalize(row["entity_name"])
        for row in rows
        if row["entity_name"]
    }


def get_or_create_entity(conn, entity_type, name):
    name = clean_text(name)

    # 저장 전에 정규화 불가능한 이름을 차단한다.
    require_normalized_name(name)

    row = conn.execute(
        """
        SELECT entity_id
        FROM entity
        WHERE entity_type = %s
          AND entity_name = %s
        LIMIT 1
        """,
        (entity_type, name),
    ).fetchone()

    if row:
        return row["entity_id"]

    row = conn.execute(
        """
        INSERT INTO entity (
            entity_type,
            entity_name,
            qid,
            entity_description
        )
        VALUES (%s, %s, NULL, NULL)
        ON CONFLICT (entity_type, entity_name)
        DO UPDATE SET
            entity_name = EXCLUDED.entity_name
        RETURNING entity_id
        """,
        (entity_type, name),
    ).fetchone()

    return row["entity_id"]



def upsert_candidate_relation(conn, content_id, candidate, source_url):
    entity_id = get_or_create_entity(
        conn,
        candidate["entity_type"],
        candidate["name"],
    )

    candidate_score = candidate.get("score")

    evidence = {
        "source_url": source_url,
        "source_section": candidate["section"],
        "excerpt": candidate["excerpt"],
        "source_quality": "curated_community",
        "score_features": candidate.get("score_features", {}),
        "source_score": candidate.get("source_score"),
        "rule_score": candidate.get("rule_score"),
        "exploration_branch": candidate.get("exploration_branch"),
        "mention_reason": candidate.get("mention_reason"),
        "relation_hint": candidate.get("relation_hint"),
        "observations": candidate.get("observations", []),
    }

    row = conn.execute(
        """
        INSERT INTO relationship (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id,
            source_name,
            fact_status,
            evidence,
            visibility,
            next_hop_eligible,
            candidate_score,
            score_version,
            scored_at
        )
        VALUES (
            'content',
            %s,
            %s,
            'entity',
            %s,
            'wikipedia_section',
            'proposed',
            %s::jsonb,
            'shown',
            %s,
            %s,
            %s,
            NOW()
        )
        ON CONFLICT (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id
        )
        DO UPDATE SET
            evidence = EXCLUDED.evidence,
            visibility = 'shown',
            next_hop_eligible = EXCLUDED.next_hop_eligible,
            candidate_score = EXCLUDED.candidate_score,
            score_version = EXCLUDED.score_version,
            scored_at = NOW()
        WHERE relationship.fact_status = 'proposed'
          AND relationship.source_name = 'wikipedia_section'
        RETURNING relationship_id
        """,
        (
            content_id,
            candidate["relation_type"],
            entity_id,
            json.dumps(evidence, ensure_ascii=False),
            bool(candidate.get("next_hop_eligible", False)),
            candidate_score,
            SCORE_VERSION,
        ),
    ).fetchone()

    # 검증된 관계 또는 다른 출처의 관계와 충돌하면 갱신하지 않는다.
    return row["relationship_id"] if row else None


def clear_previous_refined_candidates(conn, content_id):
    """해당 작품의 이전 Wikipedia 정제 후보만 숨긴다."""
    conn.execute(
        """
        UPDATE relationship
        SET visibility = 'hidden'
        WHERE source_type = 'content'
          AND source_id = %s
          AND relationship_type = ANY(%s)
          AND fact_status = 'proposed'
          AND source_name = 'wikipedia_section'
        """,
        (content_id, REFINED_RELATION_TYPES),
    )


# =========================================================
# 11. 위키백과 문서 요청 및 검증
# =========================================================

def fetch_wikitext(item):
    tmdb_id = int(item["source_ids"]["tmdb"])

    requested_title = PAGE_OVERRIDES.get(
        (item["content_type"], tmdb_id),
        item["title"],
    )

    data = request_json({
        "action": "parse",
        "format": "json",
        "formatversion": "2",
        "page": requested_title,
        "prop": "wikitext",
        "redirects": "1",
        "maxlag": "5",
    })

    parsed = data.get("parse")

    if not parsed:
        return None

    wikitext = parsed.get("wikitext")

    # 일부 API 응답 형식에 대한 호환 처리
    if isinstance(wikitext, dict):
        wikitext = wikitext.get("*")

    if not clean_text(wikitext):
        return None

    page_title = parsed.get("title", requested_title)

    return {
        "requested_title": requested_title,
        "page_title": page_title,
        "source_url": page_url(page_title),
        "wikitext": wikitext,
    }


def validate_matched_page(item, result):
    text = strip_wikitext(result["wikitext"][:6_000])
    expected_year = str(item.get("year") or "")

    if item["content_type"] == "movie":
        type_ok = any(
            word in text
            for word in ("영화", "감독", "개봉일")
        )
    else:
        type_ok = any(
            word in text
            for word in ("드라마", "방송", "방영", "연출")
        )

    year_ok = not expected_year or expected_year in text

    if type_ok:
        return (
            True,
            None if year_ok else f"연도 {expected_year} 문서 본문에서 확인 안 됨",
        )

    return False, "유형 단서 없음"


# =========================================================
# 12. 캐시 저장
# =========================================================

def save_cache(item, result, candidates, selected_sections, infobox_signals):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    path = CACHE_DIR / (
        f"{item['content_type']}_{item['source_ids']['tmdb']}.json"
    )

    payload = {
        "content_type": item["content_type"],
        "title": item["title"],
        "tmdb_id": item["source_ids"]["tmdb"],
        "page_title": result["page_title"],
        "source_url": result["source_url"],
        "wikitext": result["wikitext"],
        "infobox_signals": infobox_signals,
        "candidates": candidates,
        "selected_sections": selected_sections,
    }

    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


# =========================================================
# 13. 실행
# =========================================================

def main():
    if not SELECTION_PATH.exists():
        raise FileNotFoundError(
            f"선택 파일을 찾을 수 없습니다: {SELECTION_PATH}"
        )

    with SELECTION_PATH.open(encoding="utf-8") as file:
        items = [
            json.loads(line)
            for line in file
            if line.strip()
        ]

    total_candidates = 0
    failed_titles = []

    with psycopg.connect(
        DATABASE_URL,
        row_factory=psycopg.rows.dict_row,
    ) as conn:

        for index, item in enumerate(items, start=1):
            content_id = get_content_id(conn, item)

            if not content_id:
                failed_titles.append(item["title"])
                print(f"[{index}] content_id 없음: {item['title']}")
                continue

            try:
                result = fetch_wikitext(item)

            except Exception as error:
                failed_titles.append(item["title"])
                print(
                    f"[{index}] 위키 요청 실패: "
                    f"{item['title']} / {error}"
                )
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            if result is None:
                failed_titles.append(item["title"])
                print(f"[{index}] 위키 문서 없음: {item['title']}")
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            page_is_valid, validation_reason = validate_matched_page(
                item,
                result,
            )

            if not page_is_valid:
                failed_titles.append(item["title"])

                print(
                    f"[{index}] 위키 문서 채택 보류: "
                    f"{item['title']} → {result['page_title']} / "
                    f"{validation_reason}"
                )

                save_cache(item, result, [], [], {})
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            if validation_reason:
                print(
                    f"[{index}] 연도 확인 경고: "
                    f"{item['title']} → {result['page_title']} / "
                    f"{validation_reason}"
                )

            infobox_signals = extract_infobox_signals(
                result["wikitext"]
            )

            candidates = extract_section_candidates(
                item,
                result["wikitext"],
                infobox_signals,
            )

            verified_names = get_verified_entity_names(
                conn,
                content_id,
            )

            # 이미 검증된 개념 후보는 중복 제안하지 않는다.
            candidates = [
                candidate
                for candidate in candidates
                if not (
                    candidate["entity_type"] == "concept"
                    and normalize(candidate["name"]) in verified_names
                )
            ]

            candidates = candidates[:MAX_CANDIDATES_PER_CONTENT]

            selected_sections = extract_relevant_sections(
                result["wikitext"],
                infobox_signals,
            )

            # 이전 후보 숨김과 신규 후보 저장을 같은 트랜잭션에서 수행한다.
            clear_previous_refined_candidates(conn, content_id)

            for candidate in candidates:
                upsert_candidate_relation(
                    conn,
                    content_id,
                    candidate,
                    result["source_url"],
                )

            save_cache(
                item,
                result,
                candidates,
                selected_sections,
                infobox_signals,
            )

            total_candidates += len(candidates)

            print(
                f"[{index}] 완료: {item['title']} → "
                f"{result['page_title']} "
                f"(정제 후보 {len(candidates)}개 / "
                f"인포박스 신호 {len(infobox_signals)}개 / "
                f"선택 섹션 {len(selected_sections)}개)"
            )

            time.sleep(REQUEST_DELAY_SECONDS)

        conn.commit()

    print("\n--- 결과 ---")
    print(f"저장한 정제 후보: {total_candidates}")
    print(f"문서 없음/실패: {len(failed_titles)}")

    if failed_titles:
        print(", ".join(failed_titles))


if __name__ == "__main__":
    main()