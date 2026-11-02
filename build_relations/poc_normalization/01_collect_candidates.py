import json
from datetime import datetime
from pathlib import Path

# 프로젝트 루트
ROOT = Path(__file__).resolve().parents[2]

# LLM 추출 결과
SOURCE_DIR = ROOT / "data/enrichment/semantic_topics"

# 정규화 후보 저장 위치
OUTPUT_DIR = ROOT / "data/enrichment/normalization"

CONTENT_IDS = [42844, 79246, 84900]


def get_latest_result(content_id: int):
    """콘텐츠별 가장 최근 성공한 추출 결과를 가져온다."""

    content_dir = SOURCE_DIR / str(content_id)

    if not content_dir.exists():
        print(f"[SKIP] 결과 폴더 없음: {content_id}")
        return None

    result_files = list(content_dir.glob("*/result.json"))

    if not result_files:
        print(f"[SKIP] 결과 파일 없음: {content_id}")
        return None

    # 파일 수정 시간을 기준으로 최신 결과부터 확인
    result_files.sort(
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for path in result_files:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("status") == "success":
            return path, data

    print(f"[SKIP] 성공한 결과 없음: {content_id}")
    return None


def collect_candidates():
    candidates = []
    sources = []

    for content_id in CONTENT_IDS:
        result = get_latest_result(content_id)

        if result is None:
            continue

        path, data = result
        topics = data.get("topics", [])

        sources.append({
            "content_id": content_id,
            "result_file": str(path.relative_to(ROOT)),
            "prompt_version": data.get("prompt_version"),
        })

        for index, topic in enumerate(topics, start=1):
            candidates.append({
                "candidate_id": f"{content_id}-{index:03d}",
                "content_id": content_id,

                # LLM 원본
                "original_name": topic.get("name"),
                "type": topic.get("type"),
                "evidence": topic.get("evidence"),
                "evidence_quote": topic.get("evidence_quote"),
                "evidence_source": topic.get("evidence_source"),
                "evidence_level": topic.get("evidence_level"),
                "confidence": topic.get("confidence"),

                # 기존 형식 검증 결과
                "eligible_for_review": topic.get(
                    "eligible_for_review", False
                ),
                "review_flags": topic.get("review_flags", []),

                # 정규화 검토용 필드
                "canonical_name": None,
                "decision": "pending",
                "matched_topic_id": None,
                "review_note": None,
            })

        print(f"[OK] {content_id}: {len(topics)}개 수집")

    return {
        "created_at": datetime.now().astimezone().isoformat(),
        "sources": sources,
        "total_candidates": len(candidates),
        "candidates": candidates,
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_path = OUTPUT_DIR / "candidates.json"

    # 기존 검토 결과를 실수로 덮어쓰지 않도록 방지
    if output_path.exists():
        raise FileExistsError(
            f"이미 파일이 존재합니다: {output_path}\n"
            "기존 검토 내용을 보존하려면 파일명을 변경하세요."
        )

    data = collect_candidates()

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print()
    print(f"총 후보: {data['total_candidates']}개")
    print(f"저장 완료: {output_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()