"""설정과 콘텐츠별 위키백과 문서 매핑."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIKIPEDIA_CACHE_DIR = ROOT / "data" / "enrichment" / "wikipedia"
REFINED_CACHE_DIR = ROOT / "data" / "enrichment" / "wikipedia_refined"
WIKIPEDIA_API = "https://ko.wikipedia.org/w/api.php"

MAX_CANDIDATES_PER_CONTENT = 100
MAX_CONTEXT_CHARS = 3_000
REQUEST_DELAY_SECONDS = 1.0
WIKIMEDIA_CONTACT = os.getenv("WIKIMEDIA_CONTACT", "mailto:content-exploration@example.com")
USER_AGENT = f"content-exploration-capstone/1.0 ({WIKIMEDIA_CONTACT})"

# 키의 ID는 content_id가 아니라 각 원천의 source_id다.
PAGE_OVERRIDES = {
    ("movie", "496243"): "기생충 (영화)",
    ("movie", "11423"): "살인의 추억",
    ("movie", "282631"): "명량 (영화)",
    ("movie", "437103"): "1987 (2017년 영화)",
    ("movie", "242452"): "변호인 (영화)",
    ("movie", "382336"): "동주 (영화)",
    ("movie", "290098"): "아가씨 (영화)",
    ("movie", "397567"): "신과함께-죄와 벌",
    ("movie", "396535"): "부산행",
    ("movie", "313108"): "국제시장 (영화)",
    ("drama", "75820"): "미스터 션샤인",
    ("drama", "70593"): "킹덤 (2019년 드라마)",
    ("drama", "132925"): "옷소매 붉은 끝동",
    ("drama", "64840"): "시그널 (드라마)",
    ("drama", "61678"): "미생 (드라마)",
    ("drama", "110534"): "D.P.",
    ("drama", "197067"): "이상한 변호사 우영우",
    ("drama", "126485"): "무빙 (드라마)",
    ("drama", "214528"): "정년이 (드라마)",
    ("drama", "99494"): "악의 꽃 (2020년 드라마)",
    # YES24 goods ID. content_id와 혼동하지 말 것.
    ("book", "333133"): "구운몽",
    ("book", "119458386"): "토지 (소설)",
    ("book", "2870072"): "완득이 (소설)",
    ("book", "91901136"): "살인자의 기억법 (소설)",
    ("book", "119697570"): "아몬드 (소설)",
}

SECTION_RULES = [
    {"group": "narrative", "score": 10, "max_candidates": 15, "max_chars": 1200,
     "pattern": r"줄거리|시놉시스|내용|스토리|개요"},
    {"group": "context", "score": 8, "max_candidates": 15, "max_chars": 900,
     "pattern": r"배경|역사적 배경|시대적 배경|사회적 배경|주제|해석|의미|모티프|고증"},
    {"group": "adaptation", "score": 9, "max_candidates": 12, "max_chars": 800,
     "pattern": r"원작|각색|원작과의 차이|제작 배경|스핀오프"},
    {"group": "related", "score": 4, "max_candidates": 10, "max_chars": 500,
     "pattern": r"같이 보기|관련 작품|관련 문서"},
]

EXCLUDED_SECTION_WORDS = (
    "출연", "캐스팅", "등장인물", "제작진", "음악", "사운드트랙", "수상", "흥행",
    "평가", "평론", "시청률", "방영", "갤러리", "에피소드", "논란", "각주", "주석",
    "참고", "외부 링크", "참고 문헌", "분류", "전거 통제",
)

NOISE_PATTERNS = (
    r"^\d{4}년$", r"^\d{1,2}월$", r"^\d{1,2}일$", r"^\d{4}년 \d{1,2}월",
    r"목록$", r"편성표$", r"방송사$", r"텔레비전 네트워크$", r"신문$", r"뉴스$",
    r"엔터테인먼트$", r"기획사$", r"영화제$", r"시상식$",
)
GENERIC_LINKS = {
    "대한민국", "한국", "한국어", "영화", "드라마", "장편 영화",
    "대한민국의 영화 흥행 기록", "천만 관객 돌파 영화", "대한민국의 텔레비전 드라마 목록",
    "웹툰을 원작으로 삼은 드라마 목록", "한국 표준시", "다음",
}
INFOBOX_FIELD_ALIASES = {
    "original_work": ("원작", "원작품"),
    "creators": ("저자", "작가", "지은이", "글쓴이", "글"),
    "genres": ("장르",),
    "setting": ("배경", "시대", "시대적 배경"),
    "series": ("시리즈", "스핀오프", "후속작"),
}
REFINED_RELATION_TYPES = [
    "wikipedia_context_candidate", "wikipedia_original_candidate", "wikipedia_series_candidate",
    "wikipedia_related_work_candidate", "wikipedia_creator_candidate", "wikipedia_mention_candidate",
]
RELATED_WORK_WORDS = {
    "prequel": ("프리퀄", "프리퀼"),
    "sequel": ("속편", "후속작"),
    "spin_off": ("스핀오프",),
    "remake": ("리메이크",),
    "adaptation": ("원작", "각색", "실사화"),
}
BOX_OFFICE_WORDS = ("관객", "흥행", "돌파", "흥행수익")
FUTURE_PROJECT_WORDS = ("차기작", "차기 작품", "소문", "예상", "발표", "개발 단계")
