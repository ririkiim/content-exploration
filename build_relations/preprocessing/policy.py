"""Wikipedia 문단 선택 규칙. 원본은 변경하지 않는다."""
VERSION = "wiki-context-experiment-v1"

SECTION_KINDS = {
    "도입부": "intro", "줄거리": "plot", "내용": "plot",
    "시놉시스": "plot", "개요": "overview", "배경": "background",
    "역사적 배경": "background", "사건 개요": "background",
    "형식": "form",
}
SEPARATE_SECTIONS = {"테마", "주제", "평가", "제작", "기획", "논란"}
# 홍보 문구를 담은 문단은 자동 삭제하지 않고 선택 제외 이유를 남긴다.
PROMOTION_TERMS = (
    "베스트셀러", "판매량", "시청률", "호평", "수상", "쇄를",
    "상업적 성공", "독자를 사로잡", "최고의 걸작",
    "한국 표준시", "스트리밍", "매주",
)
