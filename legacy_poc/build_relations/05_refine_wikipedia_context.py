import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv
from common import require_normalized_name
from candidate_scoring import SCORE_VERSION, link_counts, score_candidates


ROOT = Path(__file__).resolve().parents[1]

SELECTION_PATH = (
    ROOT
    / "data"
    / "selections"
    / "selected_movie_drama_contexts.jsonl"
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
    "content-exploration-capstone/1.0 "
    f"({WIKIMEDIA_CONTACT})"
)

# 후보는 넓게 보존하고, LLM 입력 단계에서 다시 압축한다.
MAX_CANDIDATES_PER_CONTENT = 100
MAX_CONTEXT_CHARS = 3_000
REQUEST_DELAY_SECONDS = 1.0


# 동음이의어·일반 명사 페이지로 잘못 이동하는 작품만 명시한다.
PAGE_OVERRIDES = {
    # 영화
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

    # 드라마
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
            "배경",
            "역사적 배경",
            "시대적 배경",
            "사회적 배경",
            "주제",
            "해석",
            "의미",
            "모티프",
            "고증",
        ],
        "group": "context",
        "score": 8,
        "max_candidates": 15,
        "max_chars": 900,
    },
    {
        "names": [
            "원작",
            "각색",
            "원작과의 차이",
            "제작 배경",
            "스핀오프",
        ],
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
    "출연",
    "캐스팅",
    "등장인물",
    "등장 인물",
    "제작진",
    "음악",
    "사운드트랙",
    "수상",
    "흥행",
    "평가",
    "평론",
    "시청률",
    "방영",
    "갤러리",
    "에피소드",
    "논란",
    "각주",
    "주석",
    "참고",
    "외부 링크",
    "참고 문헌",
    "분류",
    "전거 통제",
}


# 전체 탐색에서 반복적으로 의미가 없는 구조적 노이즈만 제거한다.
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


# 작품 자체의 내용이 아니라 흥행·수상·차기작·제작 소식에서 나온 링크다.
# 삭제하지 않고 mention으로 보존해, 이후 LLM 판정과 표본 검수에는 쓸 수 있게 둔다.
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

# 작품 소개문에는 '개봉', '수상'이 흔하게 섞인다. 이런 넓은 단어까지
# 흥행 문맥으로 취급하면 장르·역사 인물도 같이 내려가므로, 정말 비교성
# 신호가 강한 단어만 사용한다. 수상명 자체는 아래 award_name으로 처리한다.
BOX_OFFICE_WORDS = (
    "관객", "흥행", "돌파", "흥행수익",
)
FUTURE_PROJECT_WORDS = (
    "차기작", "차기 작품", "소문", "예상", "발표", "개발 단계",
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


HEADING_PATTERN = re.compile(
    r"(?m)^(={2,6})\s*(.*?)\s*\1\s*$"
)

LINK_PATTERN = re.compile(
    r"\[\[([^|\]#]+)(?:#[^|\]]*)?(?:\|[^\]]*)?\]\]"
)
LINK_WITH_LABEL_PATTERN = re.compile(
    r"\[\[([^|\]#]+)(?:#[^|\]]*)?(?:\|([^\]]*))?\]\]"
)


def clean_text(value):
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def normalize(value):
    return require_normalized_name(clean_text(value))


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
    return (
        "https://ko.wikipedia.org/wiki/"
        + quote(page_title.replace(" ", "_"))
    )


def strip_wikitext(text):
    if not text:
        return ""

    text = re.sub(r"\{\|.*?\|\}", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>/]*?>.*?</ref>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*/>", " ", text)

    previous = None

    while previous != text:
        previous = text
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text, flags=re.DOTALL)

    text = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)

    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def split_top_level(text, separator="|"):
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


def is_noise_link(name):
    name = clean_text(name)

    if not name:
        return True

    if ":" in name:
        return True

    if name in GENERIC_LINKS:
        return True

    # 위키 원문에는 '디즈니 +'처럼 공백이 섞여 들어오는 경우가 있다.
    compact_name = re.sub(r"\s+", "", name)
    if compact_name in {
        "KBS", "MBC", "SBS", "JTBC", "tvN", "ENA", "TV조선",
        "넷플릭스", "디즈니+", "디즈니플러스", "KNN", "HBO",
        "대한민국영화", "대한민국의영화",
        "대한민국드라마", "대한민국의드라마",
    }:
        return True

    return any(
        re.match(pattern, name, flags=re.IGNORECASE)
        for pattern in NOISE_PATTERNS
    )


def mention_reason(section, link_name, local_window, is_genre_modifier=False):
    """링크 주변의 짧은 구간만 보고 부수 언급 여부를 판정한다."""
    if section in {"수상", "흥행", "시청률", "참고 사항"}:
        return "metadata_section"

    # 사람 이름(오연상 등)의 끝 글자 '상'을 수상명으로 오인하지 않는다.
    if re.search(r"(?:영화제|어워드|조합상|영화상|예술상|연기상|작품상|종려상)$", link_name):
        return "award_name"

    # '좀비 영화', '블랙 코미디 영화'처럼 작품 성격을 직접 수식하는
    # 장르 링크는 바로 뒤에 관객 수가 와도 후보로 보존한다.
    if not is_genre_modifier and any(
        word in local_window for word in BOX_OFFICE_WORDS
    ):
        return "boxoffice_near"

    if any(word in local_window for word in FUTURE_PROJECT_WORDS):
        return "future_project_near"

    return None


def related_work_hint(local_window):
    """작품형 후보가 어떤 직접 관계로 언급됐는지 힌트만 기록한다."""
    for hint, words in RELATED_WORK_WORDS.items():
        if any(word in local_window for word in words):
            return hint
    return None


def unwrap_templates(text):
    """
    {{small|원작 제목}}, {{lang|ko|작품명}}처럼
    템플릿 안에 감춰진 실제 텍스트를 최대한 보존한다.
    """
    if not text:
        return ""

    previous = None

    while previous != text:
        previous = text

        def replace_template(match):
            inner = match.group(1)
            parts = split_top_level(inner)

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

    # HTML 줄바꿈은 장르·이름 구분자로 보존한다.
    raw_value = re.sub(r"<br\s*/?>", ",", raw_value, flags=re.IGNORECASE)

    # 정년이처럼 <[[정년이]]>로 제목을 감싼 위키 문법은 HTML 태그가 아니다.
    # 먼저 링크만 보존한 제목 표기로 바꿔야 strip_wikitext가 삭제하지 않는다.
    raw_value = re.sub(
        r"<\s*(\[\[[^\]]+\]\])\s*>",
        r"《\1》",
        raw_value,
    )

    # <정년이>처럼 작품명을 표시한 꺾쇠는
    # HTML 태그로 지우지 말고 《정년이》로 바꿔 보존한다.
    raw_value = re.sub(
        r"<([가-힣A-Za-z0-9 .!?:'’\-]{2,100})>",
        r"《\1》",
        raw_value,
    )

    return strip_wikitext(
        unwrap_templates(raw_value)
    )


def extract_link_names(raw_value):
    names = []

    for match in LINK_PATTERN.finditer(raw_value or ""):
        name = clean_text(match.group(1))

        if name and not is_noise_link(name):
            names.append(name)

    return list(dict.fromkeys(names))


def extract_title_marked_values(text):
    """
    《신의 나라》, 〈작품명〉, <정년이> 형태의 제목을 찾는다.
    """
    titles = re.findall(
        r"[《〈<]([^》〉>]{2,100})[》〉>]",
        text or "",
    )

    result = []

    for title in titles:
        title = clean_text(title)

        if title and not is_noise_link(title):
            result.append(title)

    return list(dict.fromkeys(result))



def extract_creator_names(text):
    """
    예시:
    김은희, 윤인완의 웹툰 《신의 나라》
    강풀의 만화 《무빙》
    김보통의 웹툰 《D.P. 개의 날》
    주호민의 만화 신과함께
    """
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

    for part in re.split(r"[,/·ㆍ]|\s+(?:과|와|및)\s+", prefix):
        part = clean_text(part)

        if not part:
            continue

        # 공백이 든 이름(세라 워터스)을 하나의 원작자로 보존한다.
        part = re.sub(r"\([^)]*\)", "", part).strip()
        if re.fullmatch(r"[가-힣]{2,8}(?:\s+[가-힣]{2,12})*", part):
            if not is_noise_link(part):
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
    """
    원작 필드에서 원작 작품과 원작자를 분리한다.

    예시:
    김은희, 윤인완의 웹툰 《신의 나라》
    → works: [신의 나라]
    → creators: [김은희, 윤인완]

    강풀의 만화 《무빙》
    → works: [무빙]
    → creators: [강풀]

    주호민의 만화 신과함께
    → works: [신과함께]
    → creators: [주호민]
    """
    raw_value = clean_text(raw_value)

    if not raw_value:
        return {
            "works": [],
            "creators": [],
        }

    plain_text = infobox_plain_text(raw_value)

    creators = extract_creator_names(plain_text)

    # 1. 《작품명》 / 〈작품명〉 / <작품명> 표기 우선
    works = extract_title_marked_values(plain_text)

    # 2. 꺾쇠 없이 "만화 신과함께"처럼 적힌 경우
    if not works:
        media_match = re.search(
            r"(?:웹툰|만화|소설|웹소설|희곡)",
            plain_text,
        )

        if media_match:
            tail = plain_text[media_match.end():].strip()

            # "웹툰 신의 나라를 원작으로" 같은 후속 설명 제거
            tail = re.split(
                r"(?:을|를)\s*(?:원작|바탕)|"
                r"(?:을|를)\s*각색|"
                r"원작으로",
                tail,
                maxsplit=1,
            )[0]

            # 괄호 속 글/그림 정보 제거
            tail = re.sub(r"\([^)]*\)", "", tail).strip()

            # 문장부호 제거
            tail = tail.strip(" :：-–—.,")

            if (
                tail
                and 2 <= len(tail) <= 100
                and not is_noise_link(tail)
            ):
                works.append(tail)

    # 원작자 이름이 원작 작품 후보로 다시 들어가지 않게 제거
    creator_keys = {
        normalize(creator)
        for creator in creators
    }

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
        candidates.extend(
            extract_title_marked_values(plain_text)
        )
        candidates.extend(
            extract_link_names(raw_value)
        )

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

        normalized_keys = {
            normalize(key)
            for key in fields
        }

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
            alias_keys = {
                normalize(alias)
                for alias in aliases
            }

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
                        "text_candidates": (
                            extract_plain_infobox_candidates(
                                raw_value,
                                signal_name,
                            )
                        ),
                    }

                break

        if signals:
            return signals

    return {}


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

    if any(word in section_title for word in EXCLUDED_SECTION_WORDS):
        return None

    for rule in SECTION_RULES:
        if any(name in section_title for name in rule["names"]):
            return rule

    return None


def split_sections(wikitext):
    headings = list(HEADING_PATTERN.finditer(wikitext))
    sections = []

    lead_end = headings[0].start() if headings else len(wikitext)
    lead_text = wikitext[:lead_end].strip()

    if lead_text:
        sections.append(
            {
                "section": "lead",
                "text": lead_text,
            }
        )

    for index, heading in enumerate(headings):
        section_title = clean_section_title(heading.group(2))
        content_start = heading.end()

        if index + 1 < len(headings):
            content_end = headings[index + 1].start()
        else:
            content_end = len(wikitext)

        section_text = wikitext[content_start:content_end].strip()

        if section_title and section_text:
            sections.append(
                {
                    "section": section_title,
                    "text": section_text,
                }
            )

    return sections


def remove_templates_and_tables(text):
    text = re.sub(r"\{\|.*?\|\}", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>/]*?>.*?</ref>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*/>", " ", text)

    previous = None

    while previous != text:
        previous = text
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text, flags=re.DOTALL)

    return text


def strip_external_links(text):
    """[URL 표시명]은 표시명만, URL만 있는 외부 링크는 제거한다."""
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    return re.sub(r"\[https?://[^\s\]]+\]", "", text)


def extract_link_context(section_text, link_name):
    """표시 텍스트를 보존하고, 목록은 줄·본문은 같은 문장만 근거로 쓴다."""
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

    # 같이 보기 같은 목록만 줄 단위로 취급한다. 일반 본문은 줄바꿈이 있어도
    # 문장 경계를 넘어가지 않게 아래의 문장 분리 로직을 쓴다.
    for raw_line in marked.splitlines():
        if marker_start in raw_line and re.match(r"^\s*[*#:]", raw_line):
            excerpt = strip_wikitext(raw_line).strip()
            position = excerpt.find(marker_start)
            local_window = excerpt[max(0, position - 15):position + 30]
            clean_excerpt = excerpt.replace(marker_start, "").replace(marker_end, "")
            return clean_excerpt[:400], local_window, False

    plain = strip_wikitext(marked).strip()
    sentences = re.split(r"(?<=[.!?])\s+|\n+", plain)
    sentence = next(
        (value.strip() for value in sentences if marker_start in value),
        plain,
    )
    position = sentence.find(marker_start)
    if position < 0:
        return sentence[:400], "", False

    local_window = sentence[max(0, position - 15):position + 30]
    after_marker = sentence[
        sentence.find(marker_end, position) + len(marker_end):
    ]
    is_genre_modifier = bool(
        re.match(r"\s*(?:영화|드라마|작품|시리즈)", after_marker)
    )
    clean_sentence = sentence.replace(marker_start, "").replace(marker_end, "")
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

        selected_sections.append(
            {
                "section": "인포박스",
                "group": "infobox",
                "score": 9,
                "text": infobox_text,
            }
        )

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

        text = plain_text[:rule["max_chars"]]
        text = text[:remaining]

        if len(text) < 30:
            continue

        selected_sections.append(
            {
                "section": section,
                "group": rule["group"],
                "score": rule["score"],
                "text": text,
            }
        )

        total_chars += len(text)

    return selected_sections



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
        known_observations = {
            (item["section"], item["excerpt"])
            for item in previous.get("observations", [])
        }
        if observation_key not in known_observations:
            previous.setdefault("observations", []).append(observation)

        if candidate["score"] > previous["score"]:
            candidate["observations"] = previous["observations"]
            candidates_by_key[key] = candidate

    # 원작 작품 후보
    original_signal = infobox_signals.get("original_work")

    if original_signal:
        for name in original_signal.get("work_candidates", []):
            name = clean_text(name)

            if not name or is_noise_link(name):
                continue

            add_candidate(
                {
                    "name": name,
                    "section": "인포박스",
                    "group": "original_work",
                    "score": 9,
                    "excerpt": (
                        "인포박스 original_work: "
                        f"{original_signal.get('text', '')}"
                    )[:400],
                    "relation_type": "wikipedia_original_candidate",
                    "entity_type": "source_work",
                }
            )

        # 원작자 후보
        for name in original_signal.get("creator_candidates", []):
            name = clean_text(name)

            if not name or is_noise_link(name):
                continue

            add_candidate(
                {
                    "name": name,
                    "section": "인포박스",
                    "group": "original_creator",
                    "score": 8,
                    "excerpt": (
                        "인포박스 original_work: "
                        f"{original_signal.get('text', '')}"
                    )[:400],
                    "relation_type": "wikipedia_creator_candidate",
                    "entity_type": "person",
                }
            )

    # 시리즈·스핀오프 후보
    series_signal = infobox_signals.get("series")

    if series_signal:
        for name in series_signal.get("text_candidates", []):
            name = clean_text(name)

            if not name or is_noise_link(name):
                continue

            add_candidate(
                {
                    "name": name,
                    "section": "인포박스",
                    "group": "series",
                    "score": 9,
                    "excerpt": (
                        "인포박스 series: "
                        f"{series_signal.get('text', '')}"
                    )[:400],
                    "relation_type": "wikipedia_series_candidate",
                    "entity_type": "source_work",
                }
            )

    # 인포박스 장르·배경 후보
    for signal_name in ("genres", "setting"):
        signal = infobox_signals.get(signal_name)

        if not signal:
            continue

        score = 7 if signal_name == "genres" else 8

        for name in signal.get("text_candidates", []):
            name = clean_text(name)

            if not name or is_noise_link(name):
                continue

            add_candidate(
                {
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
                }
            )

    # 본문 선택 섹션의 내부 링크 후보
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

            excerpt, local_window, is_genre_modifier = extract_link_context(
                cleaned_text,
                link_name,
            )
            reason = mention_reason(
                section,
                link_name,
                local_window,
                is_genre_modifier=is_genre_modifier,
            )
            is_work = WORK_TITLE_HINT.search(link_name) is not None
            is_creator = CREATOR_TITLE_HINT.search(link_name) is not None
            relation_hint = related_work_hint(local_window) if is_work else None

            # 관계어가 있는 작품형 후보는 수상·흥행 단어보다 우선한다.
            # 그 외 작품형 후보가 흥행 비교 문맥이면 mention으로 보낸다.
            if is_creator:
                # '양우석 (영화 감독)' 같은 위키 문서 제목은 인물 관계다.
                # 괄호는 동명이인 구분자이므로 엔티티에는 이름만 저장한다.
                relation_type = "wikipedia_creator_candidate"
                entity_type = "person"
                group = "creator"
                score = max(rule["score"], 8)
                candidate_name = re.sub(r"\s*\([^)]*\)$", "", link_name)
                reason = None
            elif is_work and relation_hint:
                relation_type = "wikipedia_related_work_candidate"
                entity_type = "source_work"
                group = "related_work"
                score = max(rule["score"], 6)
                reason = None
            elif reason:
                # 작품형 후보는 흥행 비교로 발견됐더라도 같은 작품 엔티티로
                # 보존한다. relation_type을 분리하면 '반도'처럼 같은 작품이
                # related_work와 mention에 이중 저장되기 때문이다.
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
            elif is_work:
                relation_type = "wikipedia_related_work_candidate"
                entity_type = "source_work"
                group = "related_work"
                score = max(rule["score"], 6)
            else:
                relation_type = "wikipedia_context_candidate"
                entity_type = "concept"
                group = rule["group"]
                score = rule["score"]

            if not is_creator:
                candidate_name = link_name

            add_candidate(
                {
                    "name": candidate_name,
                    "section": section,
                    "group": group,
                    "score": score,
                    "excerpt": excerpt,
                    "mention_reason": reason,
                    "relation_hint": relation_hint,
                    "relation_type": relation_type,
                    "entity_type": entity_type,
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


def get_content_id(conn, item):
    row = conn.execute(
        """
        SELECT catalogue.content_id
        FROM content_catalogue AS catalogue
        JOIN content_source_map AS source_map
          ON source_map.content_id = catalogue.content_id
        WHERE source_map.source = 'tmdb'
          AND source_map.source_id = %s
          AND catalogue.content_type = %s
        ORDER BY catalogue.content_id
        LIMIT 1
        """,
        (
            str(item["source_ids"]["tmdb"]),
            item["content_type"],
        ),
    ).fetchone()

    return row["content_id"] if row else None


def get_verified_entity_names(conn, content_id):
    rows = conn.execute(
        """
        SELECT entity.normalized_name
        FROM content_entity_relation AS relation
        JOIN entity
          ON entity.entity_id = relation.entity_id
        WHERE relation.content_id = %s
          AND relation.fact_status = 'verified'
        """,
        (content_id,),
    ).fetchall()

    return {
        row["normalized_name"]
        for row in rows
        if row["normalized_name"]
    }


def get_or_create_entity(conn, entity_type, name):
    normalized_name = require_normalized_name(name)

    row = conn.execute(
        """
        SELECT entity_id
        FROM entity
        WHERE entity_type = %s
          AND normalized_name = %s
        ORDER BY entity_id
        LIMIT 1
        """,
        (entity_type, normalized_name),
    ).fetchone()

    if row:
        return row["entity_id"]

    row = conn.execute(
        """
        INSERT INTO entity (
            entity_type,
            name,
            normalized_name
        )
        VALUES (%s, %s, %s)
        RETURNING entity_id
        """,
        (entity_type, name, normalized_name),
    ).fetchone()

    return row["entity_id"]


def upsert_candidate_relation(conn, content_id, candidate, source_url):
    entity_id = get_or_create_entity(
        conn,
        entity_type=candidate["entity_type"],
        name=candidate["name"],
    )

    row = conn.execute(
        """
        INSERT INTO content_entity_relation (
            content_id,
            entity_id,
            relation_type,
            fact_status,
            review_status,
            source_section,
            candidate_score,
            source_score,
            rule_score,
            score_features,
            score_version,
            exploration_branch,
            next_hop_eligible
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
        ON CONFLICT (content_id, entity_id, relation_type)
        DO UPDATE SET
            fact_status = EXCLUDED.fact_status,
            source_section = EXCLUDED.source_section,
            -- v2는 모든 관측을 모은 뒤 계산한 점수다. 이전 실행의 더 큰
            -- 위치 점수를 남기면 규칙을 바꿔도 재계산이 되지 않는다.
            candidate_score = EXCLUDED.candidate_score,
            source_score = EXCLUDED.source_score,
            rule_score = EXCLUDED.rule_score,
            score_features = EXCLUDED.score_features,
            score_version = EXCLUDED.score_version,
            exploration_branch = EXCLUDED.exploration_branch,
            next_hop_eligible = EXCLUDED.next_hop_eligible,
            candidate_is_stale = FALSE,
            updated_at = NOW()
        RETURNING content_entity_relation_id
        """,
        (
            content_id,
            entity_id,
            candidate["relation_type"],
            "proposed",
            "not_reviewed",
            candidate["section"],
            candidate["score"],
            candidate["source_score"],
            candidate["rule_score"],
            json.dumps(candidate["score_features"], ensure_ascii=False),
            SCORE_VERSION,
            candidate["exploration_branch"],
            candidate["next_hop_eligible"],
        ),
    ).fetchone()

    relation_id = row["content_entity_relation_id"]

    conn.execute(
        """
        INSERT INTO relation_evidence (
            content_entity_relation_id,
            source_name,
            source_url,
            excerpt,
            source_quality
        )
        SELECT %s, %s, %s, %s, %s
        WHERE NOT EXISTS (
            SELECT 1
            FROM relation_evidence
            WHERE content_entity_relation_id = %s
              AND source_name = %s
              AND excerpt = %s
        )
        """,
        (
            relation_id,
            "wikipedia_section",
            source_url,
            f"[{candidate['section']}] {candidate['excerpt']}",
            "curated_community",
            relation_id,
            "wikipedia_section",
            f"[{candidate['section']}] {candidate['excerpt']}",
        ),
    )

    for observation in candidate.get("observations", []):
        conn.execute(
            """
            INSERT INTO relation_candidate_observation (
                content_entity_relation_id,
                source_name,
                source_section,
                excerpt,
                position_score,
                mention_reason,
                relation_hint
            )
            SELECT %s, %s, %s, %s, %s, %s, %s
            WHERE NOT EXISTS (
                SELECT 1
                FROM relation_candidate_observation
                WHERE content_entity_relation_id = %s
                  AND source_name = %s
                  AND source_section = %s
                  AND excerpt = %s
            )
            """,
            (
                relation_id,
                "wikipedia_section",
                observation["section"],
                observation["excerpt"],
                observation["score"],
                observation.get("mention_reason"),
                observation.get("relation_hint"),
                relation_id,
                "wikipedia_section",
                observation["section"],
                observation["excerpt"],
            ),
        )

        conn.execute(
            """
            UPDATE relation_candidate_observation
            SET mention_reason = COALESCE(%s, mention_reason),
                relation_hint = COALESCE(%s, relation_hint)
            WHERE content_entity_relation_id = %s
              AND source_name = %s
              AND source_section = %s
              AND excerpt = %s
            """,
            (
                observation.get("mention_reason"),
                observation.get("relation_hint"),
                relation_id,
                "wikipedia_section",
                observation["section"],
                observation["excerpt"],
            ),
        )


def clear_previous_refined_candidates(conn, content_id):
    """
    05 단계에서 생성한 proposed 위키 후보만 초기화한다.
    TMDb/KMDb/Wikidata 기반 verified 관계는 삭제하지 않는다.
    """
    # 관계 ID와 후속 LLM/수동 검수 기록은 보존한다.
    # 이번 실행에서 다시 발견되는 후보만 upsert로 stale=FALSE가 된다.
    conn.execute(
        """
        UPDATE content_entity_relation
        SET candidate_is_stale = TRUE,
            updated_at = NOW()
        WHERE content_id = %s
          AND relation_type = ANY(%s)
          AND fact_status = 'proposed'
        """,
        (content_id, REFINED_RELATION_TYPES),
    )


def ensure_columns(conn):
    conn.execute(
        """
        ALTER TABLE content_entity_relation
        ADD COLUMN IF NOT EXISTS source_section TEXT
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relation_candidate_observation (
            observation_id BIGSERIAL PRIMARY KEY,
            content_entity_relation_id BIGINT NOT NULL
                REFERENCES content_entity_relation(content_entity_relation_id)
                ON DELETE CASCADE,
            source_name VARCHAR(50) NOT NULL,
            source_section TEXT NOT NULL,
            excerpt TEXT NOT NULL,
            position_score INTEGER NOT NULL,
            mention_reason TEXT,
            relation_hint TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    conn.execute(
        """
        ALTER TABLE relation_candidate_observation
        ADD COLUMN IF NOT EXISTS mention_reason TEXT
        """
    )

    conn.execute(
        """
        ALTER TABLE relation_candidate_observation
        ADD COLUMN IF NOT EXISTS relation_hint TEXT
        """
    )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        uq_relation_candidate_observation
        ON relation_candidate_observation (
            content_entity_relation_id,
            source_name,
            source_section,
            excerpt
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relation_judgment (
            relation_judgment_id BIGSERIAL PRIMARY KEY,
            content_entity_relation_id BIGINT NOT NULL
                REFERENCES content_entity_relation(content_entity_relation_id)
                ON DELETE CASCADE,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            label VARCHAR(20) NOT NULL CHECK (
                label IN ('subject', 'context', 'mention', 'noise')
            ),
            evidence_sentence_idx INTEGER[],
            confidence NUMERIC(4, 3),
            reason TEXT,
            judged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (content_entity_relation_id, model, prompt_version)
        )
        """
    )

    conn.execute(
        """
        ALTER TABLE content_entity_relation
        ADD COLUMN IF NOT EXISTS candidate_score INTEGER
        """
    )

    for column_sql in (
        "ADD COLUMN IF NOT EXISTS source_score INTEGER",
        "ADD COLUMN IF NOT EXISTS rule_score INTEGER",
        "ADD COLUMN IF NOT EXISTS score_features JSONB",
        "ADD COLUMN IF NOT EXISTS score_version TEXT",
        "ADD COLUMN IF NOT EXISTS exploration_branch TEXT",
        "ADD COLUMN IF NOT EXISTS next_hop_eligible BOOLEAN NOT NULL DEFAULT FALSE",
    ):
        conn.execute(f"ALTER TABLE content_entity_relation {column_sql}")

    conn.execute(
        """
        ALTER TABLE content_entity_relation
        ADD COLUMN IF NOT EXISTS candidate_is_stale BOOLEAN NOT NULL DEFAULT FALSE
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_content_entity_relation_candidate
        ON content_entity_relation (
            content_id,
            relation_type,
            candidate_score DESC
        )
        """
    )


def fetch_wikitext(item):
    tmdb_id = int(item["source_ids"]["tmdb"])

    requested_title = PAGE_OVERRIDES.get(
        (item["content_type"], tmdb_id),
        item["title"],
    )

    data = request_json(
        {
            "action": "parse",
            "format": "json",
            "formatversion": "2",
            "page": requested_title,
            "prop": "wikitext",
            "redirects": "1",
            "maxlag": "5",
        }
    )

    parsed = data.get("parse")

    if not parsed:
        return None

    wikitext = parsed.get("wikitext")

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
    """유형만 강제하고, 위키 본문의 연도 누락은 경고로만 남긴다."""
    text = strip_wikitext(result["wikitext"][:6_000])
    expected_year = str(item.get("year") or "")

    if item["content_type"] == "movie":
        type_ok = any(word in text for word in ("영화", "감독", "개봉일"))
    else:
        type_ok = any(word in text for word in ("드라마", "방송", "방영", "연출"))

    year_ok = not expected_year or expected_year in text

    if type_ok:
        return (
            True,
            None if year_ok
            else f"연도 {expected_year} 문서 본문에서 확인 안 됨",
        )

    reason = []
    if not type_ok:
        reason.append("유형 단서 없음")
    return False, ", ".join(reason)


def save_cache(
    item,
    result,
    candidates,
    selected_sections,
    infobox_signals,
):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    path = (
        CACHE_DIR
        / f"{item['content_type']}_{item['source_ids']['tmdb']}.json"
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
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
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
        ensure_columns(conn)

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
                    f"[{index}] 위키 문서 채택 보류: {item['title']} "
                    f"→ {result['page_title']} / {validation_reason}"
                )
                save_cache(
                    item=item,
                    result=result,
                    candidates=[],
                    selected_sections=[],
                    infobox_signals={},
                )
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            if validation_reason:
                print(
                    f"[{index}] 연도 확인 경고: {item['title']} "
                    f"→ {result['page_title']} / {validation_reason}"
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

            clear_previous_refined_candidates(conn, content_id)

            for candidate in candidates:
                upsert_candidate_relation(
                    conn=conn,
                    content_id=content_id,
                    candidate=candidate,
                    source_url=result["source_url"],
                )

            save_cache(
                item=item,
                result=result,
                candidates=candidates,
                selected_sections=selected_sections,
                infobox_signals=infobox_signals,
            )

            total_candidates += len(candidates)

            print(
                f"[{index}] 완료: {item['title']} "
                f"→ {result['page_title']} "
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
