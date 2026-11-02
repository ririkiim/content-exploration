"""08. 콘텐츠별 근거 데이터를 공통 구조로 변환한다.

입력:
- exploration/input_{content_id}.json
- exploration/inventory_{content_id}.json
- normalization/keyword_qid_review_{content_id}_v1.json (선택)

출력:
- normalization/evidence_bundle_{content_id}_v1.json

QID를 자동 선택하거나 의미적 관련성을 판정하지 않는다.
"""

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path("data/enrichment")
EXPLORATION_DIR = BASE_DIR / "exploration"
NORMALIZATION_DIR = BASE_DIR / "normalization"


def read_json(path, required=True):
    if not path.exists():
        if required:
            raise FileNotFoundError(f"필수 입력 파일 없음: {path}")
        return None

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def unique_documents(input_data):
    documents = []
    seen_ids = set()

    def add_document(document):
        evidence_id = document["evidence_id"]

        if evidence_id in seen_ids:
            return

        seen_ids.add(evidence_id)
        documents.append(document)

    description = input_data.get("description")

    if isinstance(description, str) and description.strip():
        add_document({
            "evidence_id": (
                f"content_description:{input_data['content_id']}"
            ),
            "source_kind": "content_description",
            "source": "exploration_input",
            "text": description,
            "source_url": None,
            "revision_id": None,
        })

    for field in (
        "wiki_paragraphs",
        "wiki_reference_paragraphs",
    ):
        paragraphs = input_data.get(field) or []

        for index, paragraph in enumerate(paragraphs):
            if not isinstance(paragraph, dict):
                continue

            text = paragraph.get("text")

            if not isinstance(text, str) or not text.strip():
                continue

            evidence_id = paragraph.get("source_id")

            if not evidence_id:
                evidence_id = (
                    f"{field}:{input_data['content_id']}:{index}"
                )

            add_document({
                "evidence_id": evidence_id,
                "source_kind": paragraph.get(
                    "source_kind", field
                ),
                "source": "wikipedia",
                "section": paragraph.get("section"),
                "text": text,
                "source_url": paragraph.get("source_url"),
                "revision_id": paragraph.get("revision_id"),
            })

    return documents


def get_review_index(review_data):
    if not isinstance(review_data, dict):
        return {}

    return {
        row["keyword"].strip().casefold(): row
        for row in review_data.get("db_keywords", [])
        if isinstance(row, dict)
        and isinstance(row.get("keyword"), str)
    }


def extract_keywords(inventory, review_data):
    review_index = get_review_index(review_data)
    keywords = []

    for tag in inventory.get("source_tags", []):
        if not isinstance(tag, dict):
            continue

        if tag.get("kind") != "source_keyword":
            continue

        name = tag.get("name")

        if not isinstance(name, str) or not name.strip():
            continue

        sources = []

        for evidence in tag.get("evidence", []):
            if not isinstance(evidence, dict):
                continue

            sources.append({
                "source": evidence.get("source"),
                "source_keyword_id": evidence.get("source_id"),
                "field": evidence.get("field"),
                "relation_claim": evidence.get("relation_claim"),
            })

        review = review_index.get(name.strip().casefold())

        if review is None:
            review_status = "not_reviewed"
            eligible_candidates = []
            excluded_candidates = []
        else:
            review_status = review.get(
                "review_status", "unknown"
            )
            eligible_candidates = review.get(
                "eligible_candidates", []
            )
            excluded_candidates = review.get(
                "excluded_candidates", []
            )

        keywords.append({
            "keyword": name,
            "kind": "source_keyword",
            "sources": sources,
            "qid_review_status": review_status,
            "qid_candidates": eligible_candidates,
            "excluded_qid_candidates": excluded_candidates,
            "selected_qid": None,
            "evidence_document_ids": [],
            "evidence_link_status": "not_evaluated",
        })

    return keywords


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--content-id",
        type=int,
        required=True,
    )

    args = parser.parse_args()
    cid = args.content_id

    input_path = EXPLORATION_DIR / f"input_{cid}.json"
    inventory_path = EXPLORATION_DIR / f"inventory_{cid}.json"

    review_path = (
        NORMALIZATION_DIR
        / f"keyword_qid_review_{cid}_v1.json"
    )

    output_path = (
        NORMALIZATION_DIR
        / f"evidence_bundle_{cid}_v1.json"
    )

    input_data = read_json(input_path)
    inventory = read_json(inventory_path)
    review_data = read_json(review_path, required=False)

    if input_data.get("content_id") != cid:
        raise ValueError("input 파일의 content_id 불일치")

    if inventory.get("content_id") != cid:
        raise ValueError("inventory 파일의 content_id 불일치")

    if (
        review_data is not None
        and review_data.get("content_id") != cid
    ):
        raise ValueError("06 결과의 content_id 불일치")

    documents = unique_documents(input_data)
    keywords = extract_keywords(inventory, review_data)

    warnings = []

    if review_data is None:
        warnings.append(
            "06 결과 파일이 없어 QID 후보를 연결하지 않았음"
        )

    if not keywords:
        warnings.append(
            "원본 source_keyword가 없음. "
            "토픽 추출은 별도 단계에서 처리해야 함"
        )

    if not documents:
        warnings.append(
            "설명 및 Wikipedia 근거 문서가 없음"
        )

    status_counts = Counter(
        keyword["qid_review_status"]
        for keyword in keywords
    )

    output = {
        "schema_version": "evidence_bundle_v1",
        "content_id": cid,
        "content_type": input_data.get("content_type"),
        "title": input_data.get("title"),
        "release_year": input_data.get("release_year"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "genres": input_data.get("genres") or [],
        "source_keywords": keywords,
        "evidence_documents": documents,
        "summary": {
            "keyword_count": len(keywords),
            "document_count": len(documents),
            "qid_review_statuses": dict(status_counts),
        },
        "input_files": {
            "input": str(input_path),
            "inventory": str(inventory_path),
            "qid_review": (
                str(review_path)
                if review_data is not None
                else None
            ),
        },
        "warnings": warnings,
        "limitations": [
            "근거 문서와 키워드의 의미적 관련성을 판정하지 않음",
            "QID 후보의 의미 적합성을 검증하지 않음",
            "QID를 자동 선택하지 않음",
            "원본 키워드가 없는 콘텐츠에 토픽을 생성하지 않음",
        ],
    }

    NORMALIZATION_DIR.mkdir(parents=True, exist_ok=True)

    with output_path.open("x", encoding="utf-8") as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n콘텐츠: {output['title']} ({cid})")
    print(f"원본 키워드: {len(keywords)}개")
    print(f"근거 문서: {len(documents)}개")
    print(f"QID 검토 상태: {dict(status_counts)}")

    if warnings:
        print("\n주의:")
        for warning in warnings:
            print(f"- {warning}")

    print(f"\n저장: {output_path}")


if __name__ == "__main__":
    main()