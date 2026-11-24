"""Common title normalization utilities for book author matching.

Used for:
    - Existing DB book title comparison
    - NLK bibliographic title search
    - Series title comparison

Original titles must be preserved.
Normalized titles are comparison/search keys only.
"""

import re
import unicodedata

# =========================================================
# 1. Configuration
# =========================================================

POLICY_VERSION = 'book-title-normalization-v3'

MATCH_SCORES = {
    'same_content': 100,
    'exact_title': 95,
    'normalized_title': 90,
    'series_title': 70,
    'partial_title': 40,
    'name_only': 0,
}

STRONG_TITLE_MATCHES = {
    'same_content',
    'exact_title',
    'normalized_title',
}

EDITION_TERMS = [
    '개정증보판',
    '개정증보',
    '초판본',
    '초판',
    '복각판',
    '복간판',
    '개정판',
    '증보판',
    '개정본',
    '완역판',
    '완역본',
    '리마스터판',
    '리마스터',
    '예스리커버',
    '리커버판',
    '리커버',
    '특별합본호',
    '특별합본판',
    '특별 합본호',
    '특별 합본판',
    '합본호',
    '합본판',
    '합본',
    '특별판',
    '한정판',
    '소장판',
    '보급판',
    '기념판',
    '양장본',
    '양장판',
    '문고판',
    '무삭제판',
    '디럭스판',
    '미니북',
    '팝업북',
    '포켓북',
    '리커버 에디션',
    '리마스터 에디션',
    '특별 에디션',
    '한정 에디션',
    '소장 에디션',
    '스페셜 에디션',
    '디럭스 에디션',
    '오리지널 에디션',
]

EDITION_PATTERN = '|'.join(
    re.escape(term)
    for term in sorted(
        EDITION_TERMS,
        key=len,
        reverse=True,
    )
)

ANNIVERSARY_PATTERN = (
    r'(?:출간\s*)?'
    r'\d[\d,]*\s*'
    r'(?:주년|만\s*부|천\s*부|부)'
    r'\s*(?:판매\s*)?기념'
)

BRACKET_PATTERN = (
    r'\([^()]*\)'
    r'|\[[^\[\]]*\]'
    r'|【[^【】]*】'
)

SERIES_PATTERNS = [
    # 전지적 독자 시점 PART 2 - 03
    r'\s+PART\s*\d+'
    r'(?:\s*[-–—]\s*\d+)?'
    r'(?:\s*권)?\s*$',

    # 작품명 제2권
    r'\s+제\s*\d+\s*[권부편]\s*$',

    # 작품명 2권 / 2부
    r'\s+\d+\s*[권부편]\s*$',

    # 작품명 2
    r'\s+\d{1,3}\s*$',

    # 작품명 상 / 중 / 하
    r'\s+[상중하]\s*$',

    # 작품명 (상)
    r'\s*\([상중하]\)\s*$',

    # 작품명 (2권)
    r'\s*\(\d+\s*[권부편]\)\s*$',

    # 작품명 Vol.2
    r'\s+VOL\.?\s*\d+\s*$',

    # 작품명 Volume 2
    r'\s+VOLUME\s*\d+\s*$',
]

# =========================================================
# 2. Basic normalization
# =========================================================

def clean(value):

    return re.sub(
        r'\s+',
        ' ',
        str(value or ''),
    ).strip()

def unicode_normalize(value):

    return unicodedata.normalize(
        'NFKC',
        clean(value),
    )

def exact_title_key(title):
    """Basic Unicode, whitespace and case normalization."""

    return unicode_normalize(title).casefold()

def compact_title_key(title):
    """Ignore spacing and selected middle-dot characters."""

    value = unicode_normalize(title).casefold()

    return re.sub(
        r'[\s·ㆍ・･．]+',
        '',
        value,
    )

# =========================================================
# 3. Edition descriptor detection
# =========================================================

def is_edition_descriptor(value):
    """Identify edition-related text.

    Arbitrary subtitles and bracketed descriptions
    must not be removed.
    """

    value = unicode_normalize(value).casefold()

    if not value:
        return False

    # Explicit edition terms.
    if re.fullmatch(
        rf'(?:{EDITION_PATTERN})',
        value,
        flags=re.I,
    ):
        return True

    # Named editions:
    # 미나리마 에디션, 숲의 시선 에디션, etc.
    if re.fullmatch(
        r'.{0,50}(?:에디션|edition)',
        value,
        flags=re.I,
    ):
        return True

    # Anniversary and sales-related descriptions.
    if re.match(
        ANNIVERSARY_PATTERN,
        value,
        flags=re.I,
    ):
        return True

    # Combinations of edition terms.
    # Examples:
    # 리커버 한정판
    # 초판 완역본
    # 리커버 개정판
    remainder = re.sub(
        EDITION_PATTERN,
        '',
        value,
        flags=re.I,
    )

    remainder = re.sub(
        r'[\s,:：\-–—/]+',
        '',
        remainder,
    )

    if not remainder:
        return True

    # Cover-design descriptions.
    if (
        re.search(
            r'초판본|초판|리커버|오리지널',
            value,
        )
        and re.search(
            r'표지|커버|디자인',
            value,
        )
    ):
        return True

    # Edition description followed by another edition name.
    # Example:
    # 200만 부 기념 합본호 : 아메리칸드림 에디션
    parts = re.split(
        r'\s*[:：]\s*',
        value,
    )

    if len(parts) > 1:

        if all(
            is_edition_descriptor(part)
            for part in parts
            if part
        ):
            return True

    return False

# =========================================================
# 4. Edition normalization
# =========================================================

def remove_edition_brackets(title):
    """Remove brackets only when they describe editions."""

    def replace(match):

        original = match.group(0)
        inner = original[1:-1].strip()

        if is_edition_descriptor(inner):
            return ' '

        return original

    previous = None

    while previous != title:

        previous = title

        title = re.sub(
            BRACKET_PATTERN,
            replace,
            title,
        )

    return title

def remove_edition_colon_suffix(title):
    """Remove edition descriptions following a colon.

    Preserve meaningful subtitles.
    """

    parts = re.split(
        r'\s*[:：]\s*',
        title,
        maxsplit=1,
    )

    if len(parts) != 2:
        return title

    main_title, suffix = parts

    if (
        main_title.strip()
        and is_edition_descriptor(suffix)
    ):
        return main_title.strip()

    return title

def normalize_title(title):
    """Remove edition and marketing descriptors.

    Preserve:
        - Volume numbers
        - PART numbers
        - Meaningful subtitles
        - Non-edition bracketed descriptions
    """

    original = unicode_normalize(title)

    if not original:
        return ''

    value = original

    # Edition-related brackets.
    value = remove_edition_brackets(value)

    # Edition-related colon suffixes.
    value = remove_edition_colon_suffix(value)

    # Prefix:
    # 초판본 싯다르타
    prefix_pattern = (
        rf'^\s*(?:{EDITION_PATTERN})'
        rf'(?=\s|[:：\-–—]|$)'
        rf'\s*[:：\-–—]?\s*'
    )

    # Suffix:
    # 어린 왕자 팝업북
    # 죽이고 싶은 아이 특별 합본호
    suffix_pattern = (
        rf'(?:\s+|[:：\-–—]\s*)'
        rf'(?:{EDITION_PATTERN})\s*$'
    )

    previous = None

    while previous != value:

        previous = value

        value = re.sub(
            prefix_pattern,
            '',
            value,
            flags=re.I,
        )

        value = re.sub(
            suffix_pattern,
            '',
            value,
            flags=re.I,
        )

        value = value.strip()

    # Trailing anniversary descriptions.
    value = re.sub(
        rf'\s+{ANNIVERSARY_PATTERN}'
        rf'(?:\s+(?:{EDITION_PATTERN}|에디션))*'
        rf'\s*$',
        '',
        value,
        flags=re.I,
    )

    value = re.sub(
        r'\s+',
        ' ',
        value,
    ).strip()

    value = value.strip(
        ' \t-–—:：,'
    )

    # Never return an empty title after normalization.
    return value or original

def normalized_title_key(title):
    """Comparison key for edition-normalized titles."""

    return compact_title_key(
        normalize_title(title)
    )

# =========================================================
# 5. Series normalization
# =========================================================

def normalize_series_title(title):
    """Remove trailing volume and PART markers.

    Series-level matches are not exact work matches.
    """

    original = normalize_title(title)

    if not original:
        return ''

    value = original
    previous = None

    while previous != value:

        previous = value

        for pattern in SERIES_PATTERNS:

            value = re.sub(
                pattern,
                '',
                value,
                flags=re.I,
            )

        value = value.strip()

    # Avoid overly short or empty series keys.
    if len(compact_title_key(value)) < 3:
        return original

    return value

def series_title_key(title):

    return compact_title_key(
        normalize_series_title(title)
    )

# =========================================================
# 6. External bibliographic search variants
# =========================================================

def title_variants(title):
    """Generate title variants for external search.

    Search variants are retrieval keys only.
    They must not be treated as verified identity matches.
    """

    original = clean(title)

    if not original:
        return []

    result = []

    def add(value):

        value = clean(value)

        if value and value not in result:
            result.append(value)

    # 1. Original title.
    add(original)

    # 2. Unicode-normalized title.
    add(unicode_normalize(original))

    # 3. Edition-normalized title.
    normalized = normalize_title(original)

    add(normalized)

    # 4. Series title.
    add(
        normalize_series_title(normalized)
    )

    # 5. Main title and subtitle.
    parts = re.split(
        r'\s*[:：]\s*',
        normalized,
        maxsplit=1,
    )

    if len(parts) == 2:

        main_title, subtitle = parts

        if len(compact_title_key(main_title)) >= 2:

            add(main_title)

            add(
                normalize_series_title(
                    main_title
                )
            )

        # Subtitle-only searches are allowed for retrieval,
        # but require separate verification.
        if (
            len(compact_title_key(subtitle)) >= 4
            and not is_edition_descriptor(subtitle)
        ):
            add(subtitle)

    return result

# =========================================================
# 7. Partial title matching
# =========================================================

def partial_title_match(title_a, title_b):
    """Conservative substring matching.

    Partial matches require manual or external verification.
    """

    a = normalized_title_key(title_a)
    b = normalized_title_key(title_b)

    if not a or not b:
        return False

    if a == b:
        return False

    shorter, longer = sorted(
        [a, b],
        key=len,
    )

    if len(shorter) < 4:
        return False

    if shorter not in longer:
        return False

    if len(shorter) / len(longer) < 0.65:
        return False

    return True

# =========================================================
# 8. Title comparison
# =========================================================

def compare_titles(
    requested_title,
    existing_title,
    requested_content_id=None,
    existing_content_id=None,
):
    """Return the strongest title comparison method."""

    # 1. Same content record.
    if (
        requested_content_id is not None
        and existing_content_id is not None
        and requested_content_id == existing_content_id
    ):
        return 'same_content'

    if (
        not clean(requested_title)
        or not clean(existing_title)
    ):
        return 'name_only'

    # 2. Exact title.
    if (
        exact_title_key(requested_title)
        == exact_title_key(existing_title)
    ):
        return 'exact_title'

    # 3. Edition-normalized title.
    requested_normalized = normalized_title_key(
        requested_title
    )

    existing_normalized = normalized_title_key(
        existing_title
    )

    if (
        requested_normalized
        and requested_normalized == existing_normalized
    ):
        return 'normalized_title'

    # 4. Same series.
    requested_series = series_title_key(
        requested_title
    )

    existing_series = series_title_key(
        existing_title
    )

    if (
        requested_series
        and requested_series == existing_series
        and len(requested_series) >= 3
    ):
        return 'series_title'

    # 5. Partial title match.
    if partial_title_match(
        requested_title,
        existing_title,
    ):
        return 'partial_title'

    return 'name_only'

# =========================================================
# 9. Self-test
# =========================================================

def main():

    test_titles = [
        '초판본 수레바퀴 아래서',
        '초판본 싯다르타 (리커버 한정판)',
        '두근두근 내 인생 (리마스터판)',
        '죽이고 싶은 아이 특별 합본호',
        '1984 2 (미니북)',
        '어린 왕자 팝업북',
        '해리 포터와 불의 잔 : 인터랙티브 에디션',
        '해리 포터와 마법사의 돌 : 미나리마 에디션',
        '달러구트 꿈 백화점 (200만 부 기념 합본호 : 아메리칸드림 에디션)',
        '곰탕 2 (10만 부 판매 기념 에디션)',
        '전지적 독자 시점 PART 2 - 03',
        '말하고 싶은 비밀 Vol.2',
        '초판본 셜록홈즈 2 : 네 개의 서명',
        '해리 포터와 저주받은 아이 2부 (연극 대본)',
        '초판본 하늘과 바람과 별과 詩 : 윤동주 유고시집',
        '삼체 X : 관상지주',
    ]

    print('=== TITLE NORMALIZATION TEST ===')

    for title in test_titles:

        print('\n[ORIGINAL]', title)

        print(
            '[NORMALIZED]',
            normalize_title(title),
        )

        print(
            '[SERIES]',
            normalize_series_title(title),
        )

        print(
            '[SEARCH VARIANTS]',
            title_variants(title),
        )

    test_pairs = [
        (
            '초판본 싯다르타 (리커버 한정판)',
            '싯다르타',
            'normalized_title',
        ),
        (
            '인간실격 (오리지널 초판본 표지 디자인)',
            '인간 실격',
            'normalized_title',
        ),
        (
            '죽이고 싶은 아이 특별 합본호',
            '죽이고 싶은 아이',
            'normalized_title',
        ),
        (
            '달러구트 꿈 백화점 합본호',
            '달러구트 꿈 백화점 1',
            'series_title',
        ),
        (
            '전지적 독자 시점 PART 2 - 03',
            '전지적 독자 시점 PART 1 - 01',
            'series_title',
        ),
        (
            '1984 2 (미니북)',
            '1984',
            'series_title',
        ),
        (
            '아메리칸 학원 1',
            '파친코 1',
            'name_only',
        ),
        (
            '초판본 셜록홈즈 2 : 네 개의 서명',
            '셜록홈즈 2 : 네 개의 서명',
            'normalized_title',
        ),
    ]

    print('\n=== TITLE COMPARISON TEST ===')

    for title_a, title_b, expected in test_pairs:

        actual = compare_titles(
            title_a,
            title_b,
        )

        print(
            f'[{actual}] '
            f'{title_a} <-> {title_b}'
        )

        assert actual == expected, (
            f'Expected {expected}, got {actual}'
        )

    assert normalize_title(
        '삼체 X : 관상지주'
    ) == '삼체 X : 관상지주'

    assert normalize_title(
        '해리 포터와 저주받은 아이 2부 (연극 대본)'
    ) == '해리 포터와 저주받은 아이 2부 (연극 대본)'

    print('\n[DONE] All comparison tests passed.')

if __name__ == '__main__':
    main()