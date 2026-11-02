"""07. QID 의미 검증에 사용할 근거 데이터 진단.

- DB 접근 없음
- API 호출 없음
- LLM 호출 없음
- QID 자동 선택 없음
- 기존 파일 덮어쓰기 없음
"""

import argparse
import json

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


NORMALIZATION_DIR = Path("data/enrichment/normalization")
EXPLORATION_DIR = Path("data/enrichment/exploration")


def load_json(path):
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def summarize_value(value):
    """값의 존재 여부와 자료형만 기록한다."""

    if value is None:
        return {
            "available": False,
            "type": "null",
        }

    if isinstance(value, dict):
        return {
            "available": bool(value),
            "type": "dict",
            "keys": sorted(value.keys()),
        }

    if isinstance(value, list):
        return {
            "available": bool(value),
            "type": "list",
            "count": len(value),
        }

    if isinstance(value, str):
        return {
            "available": bool(value.strip()),
            "type": "string",
            "length": len(value),
        }

    return {
        "available": True,
        "type": type(value).__name__,
    }


def inspect_file(path):
    data = load_json(path)

    if data is None:
        return {
            "exists": False,
            "path": str(path),
        }

    if not isinstance(data, dict):
        return {
            "exists": True,
            "path": str(path),
            "root_type": type(data).__name__,
        }

    return {
        "exists": True,
        "path": str(path),
        "root_type": "dict",
        "fields": {
            key: summarize_value(value)
            for key, value in data.items()
        },
    }


def inspect_keywords(review):
    results = []

    for row in review.get("db_keywords", []):
        candidates = row.get("eligible_candidates", [])

        results.append({
            "keyword": row["keyword"],
            "review_status": row["review_status"],
            "candidate_count": len(candidates),
            "candidate_evidence": [
                {
                    "qid": candidate["qid"],
                    "has_labels": bool(
                        candidate.get("labels")
                    ),
                    "has_descriptions": bool(
                        candidate.get("descriptions")
                    ),
                    "has_p31": bool(
                        candidate.get("instance_of_qids")
                    ),
                    "has_wikipedia_evidence": any(
                        evidence.get("candidate_kind")
                        is not None
                        for evidence in row.get(
                            "wikipedia_evidence", []
                        )
                    ),
                }
                for candidate in candidates
            ],
            "missing_context": [
                "verified_keyword_sense",
                "verified_content_candidate_relation",
            ],
        })

    return results


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--content-id",
        type=int,
        required=True,
    )

    args = parser.parse_args()
    cid = args.content_id

    review_path = (
        NORMALIZATION_DIR
        / f"keyword_qid_review_{cid}_v1.json"
    )

    input_path = (
        EXPLORATION_DIR
        / f"input_{cid}.json"
    )

    inventory_path = (
        EXPLORATION_DIR
        / f"inventory_{cid}.json"
    )

    catalogue_path = (
        EXPLORATION_DIR
        / f"catalogue_{cid}.json"
    )

    audit_path = (
        EXPLORATION_DIR
        / f"audit_{cid}.json"
    )

    output_path = (
        NORMALIZATION_DIR
        / f"qid_context_audit_{cid}_v1.json"
    )

    review = load_json(review_path)

    if review is None:
        raise FileNotFoundError(review_path)

    if output_path.exists():
        raise FileExistsError(output_path)

    keyword_results = inspect_keywords(review)

    status_counts = Counter(
        row["review_status"]
        for row in keyword_results
    )

    output = {
        "content_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy": "qid_context_evidence_audit_v1",
        "files": {
            "input": inspect_file(input_path),
            "inventory": inspect_file(inventory_path),
            "catalogue": inspect_file(catalogue_path),
            "audit": inspect_file(audit_path),
        },
        "summary": {
            "total_keywords": len(keyword_results),
            "statuses": dict(status_counts),
        },
        "keywords": keyword_results,
        "limitations": [
            "파일 필드의 존재 여부만 검사한다.",
            "필드가 존재해도 의미적 관련성이 검증된 것은 아니다.",
            "후보의 P31은 콘텐츠와의 관계를 증명하지 않는다.",
            "Wikipedia 링크의 존재만으로 관련성을 확정하지 않는다.",
            "QID 선택이나 관계 생성은 수행하지 않는다.",
        ],
    }

    with output_path.open("x", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print("\n=== 맥락 데이터 파일 ===")

    for name, info in output["files"].items():
        print(
            f"{name:<12} "
            f"{'있음' if info['exists'] else '없음'}"
        )

        if info.get("fields"):
            print(
                "  필드:",
                ", ".join(info["fields"].keys()),
            )

    print("\n=== 키워드 검토 상태 ===")

    for status, count in status_counts.items():
        print(f"{status}: {count}")

    print(f"\n저장: {output_path}")


if __name__ == "__main__":
    main()