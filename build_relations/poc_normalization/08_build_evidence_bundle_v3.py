"""08. 콘텐츠별 토픽·개체 후보 및 근거 통합 v2.

입력:
- exploration/input_{cid}.json
- exploration/inventory_{cid}.json
- normalization/keyword_qid_review_{cid}_v2.json
- wikidata/enriched_{cid}.json

출력:
- normalization/evidence_bundle_{cid}_v2.json

정책:
- DB / API / LLM 사용 없음
- QID 자동 확정 없음
- 점수 계산 없음
- 기존 파일 덮어쓰기 없음
- 특정 작품·키워드·정답 QID 예외 없음
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("data/enrichment")
EXPLORATION = BASE / "exploration"
NORMALIZATION = BASE / "normalization"
WIKIDATA = BASE / "wikidata"

# Wikipedia 문서의 섹션 제목에 적용하는 일반 규칙.
# 작품별 제목이나 QID는 포함하지 않는다.
THEME_SECTIONS = {
    "테마", "주제", "주제 의식", "주제의식",
    "themes", "theme", "themes and motifs",
    "motifs", "subject matter",
}

# 문서에 명시된 '항목명: 설명' 구조만 추출한다.
THEME_PATTERN = re.compile(r"^\s*([^:\n：]{2,80})\s*[:：]\s*(.+)$", re.S)


def load_json(path, required=False):
    if not path.exists():
        if required:
            raise FileNotFoundError(f"필수 입력 파일 없음: {path}")
        return None

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize(value):
    return " ".join(str(value or "").casefold().split())


def is_qid(value):
    return isinstance(value, str) and re.fullmatch(r"Q[1-9]\d*", value) is not None


def add_document(documents, paragraph):
    """Wikipedia 문단을 원본 메타데이터와 함께 보존한다."""
    if not isinstance(paragraph, dict):
        return None

    text = paragraph.get("text")
    if not isinstance(text, str) or not text.strip():
        return None

    evidence_id = paragraph.get("source_id")
    if not evidence_id:
        return None

    if evidence_id not in documents:
        documents[evidence_id] = {
            "evidence_id": evidence_id,
            "source": "wikipedia",
            "source_kind": paragraph.get("source_kind"),
            "section": paragraph.get("section"),
            "text": text,
            "source_url": paragraph.get("source_url"),
            "revision_id": paragraph.get("revision_id"),
        }

    return evidence_id


def make_candidate(
    text,
    origin,
    relation_claim,
    qid_candidates=None,
    evidence_document_ids=None,
    source_details=None,
):
    return {
        "text": text,
        "candidate_origin": origin,
        "relation_claim": relation_claim,
        "qid_candidates": qid_candidates or [],
        "evidence_document_ids": evidence_document_ids or [],
        "source_details": source_details or [],
        "selected_qid": None,
        "review_status": "pending",
    }


def collect_source_keywords(inventory, review):
    """06번 v2의 원본 키워드와 QID 후보를 손실 없이 가져온다.

    inventory는 원본 키워드의 존재 여부를 대조하는 데 사용한다.
    QID 선택, 점수화, 의미 검증은 수행하지 않는다.
    """

    inventory_index = {}

    for tag in inventory.get("source_tags", []):
        if not isinstance(tag, dict):
            continue

        if tag.get("kind") != "source_keyword":
            continue

        name = tag.get("name")

        if not isinstance(name, str) or not name.strip():
            continue

        key = normalize(name)
        inventory_index.setdefault(key, []).append(tag)

    candidates = []

    for row in review.get("db_keywords", []):
        if not isinstance(row, dict):
            continue

        keyword = row.get("keyword")

        if not isinstance(keyword, str) or not keyword.strip():
            continue

        key = normalize(keyword)

        candidates.append(
            make_candidate(
                text=keyword,
                origin="source_keyword",
                relation_claim="source_tagged_with",
                qid_candidates=row.get("candidates", []),
                source_details=[{
                    "source_type": "catalogue_keyword",
                    "source_evidence": row.get("source_evidence", []),
                    "wikipedia_evidence": row.get(
                        "wikipedia_evidence", []
                    ),
                    "source_match_status": row.get(
                        "source_match_status"
                    ),
                    "source_matched_qid": row.get(
                        "source_matched_qid"
                    ),
                    "source_match_reason": row.get(
                        "source_match_reason"
                    ),
                    "original_inventory_tags": inventory_index.get(
                        key, []
                    ),
                    "inventory_match_found": key in inventory_index,
                    "qid_review_status": row.get("review_status"),
                }],
            )
        )

    return candidates


def collect_wikipedia_themes(input_data, documents):
    """주제 관련 섹션의 명시적인 '항목명: 설명'을 후보로 추출한다."""
    candidates = []

    paragraphs = (
        input_data.get("wiki_paragraphs", [])
        + input_data.get("wiki_reference_paragraphs", [])
    )

    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue

        section = normalize(paragraph.get("section"))

        if section not in THEME_SECTIONS:
            continue

        text = paragraph.get("text", "")
        if not isinstance(text, str):
            continue

        match = THEME_PATTERN.match(text.strip())

        if not match:
            continue

        label = match.group(1).strip()
        description = match.group(2).strip()

        if not label or not description:
            continue

        evidence_id = paragraph.get("source_id")

        if evidence_id not in documents:
            continue

        candidates.append(
            make_candidate(
                text=label,
                origin="wikipedia_explicit_theme",
                relation_claim="wikipedia_describes_as_theme",
                evidence_document_ids=[evidence_id],
                source_details=[{
                    "source": "wikipedia",
                    "section": paragraph.get("section"),
                    "source_kind": paragraph.get("source_kind"),
                    "extraction_method": "section_and_label_colon_description",
                    "extracted_label": label,
                    "description": description,
                    "source_url": paragraph.get("source_url"),
                    "revision_id": paragraph.get("revision_id"),
                }],
            )
        )

    return candidates


def collect_wikidata_subjects(enriched):
    """Wikidata 작품 항목의 P921 관계만 주제 후보로 수집한다."""
    if not enriched:
        return []

    wd = enriched.get("wikidata") or {}
    claims = wd.get("claims") or []
    work_qid = wd.get("qid")

    candidates = []

    for claim in claims:
        if not isinstance(claim, dict):
            continue

        if claim.get("property_id") != "P921":
            continue

        target_qid = claim.get("target_qid")

        if not is_qid(target_qid):
            continue

        label = claim.get("target_label") or target_qid

        candidates.append(
            make_candidate(
                text=label,
                origin="wikidata_subject",
                relation_claim="P921",
                qid_candidates=[{
                    "qid": target_qid,
                    "labels": [label],
                    "descriptions": (
                        [claim["target_description"]]
                        if claim.get("target_description")
                        else []
                    ),
                    "candidate_source": "wikidata_direct_statement",
                }],
                source_details=[{
                    "source": "wikidata",
                    "work_qid": work_qid,
                    "property_id": "P921",
                    "target_qid": target_qid,
                    "target_available": claim.get("target_available"),
                    "source_url": claim.get("source_url") or wd.get("source_url"),
                    "statement": claim,
                }],
            )
        )

    return candidates


def collect_wikipedia_links(review):
    """Wikipedia 링크는 주제가 아닌 관계 미확정 후보로 보존한다."""
    candidates = []

    for item in review.get("wikipedia_unmatched", []):
        if not isinstance(item, dict):
            continue

        qid = item.get("qid")

        if not is_qid(qid):
            continue

        links = item.get("links") or []
        if not links:
            continue

        first = links[0]
        label = (
            first.get("canonical_title")
            or first.get("label")
            or qid
        )

        candidates.append(
            make_candidate(
                text=label,
                origin="wikipedia_link",
                relation_claim="linked_from_article",
                qid_candidates=[{
                    "qid": qid,
                    "labels": [label],
                    "candidate_source": "wikipedia_link",
                }],
                source_details=[{
                    "source": "wikipedia",
                    "qid": qid,
                    "links": links,
                    "verification_role": "candidate_discovery_only",
                }],
            )
        )

    return candidates


def deduplicate(candidates):
    """동일 출처·관계·표현·QID의 중복만 병합한다.

    서로 다른 출처의 후보는 합치지 않는다.
    출처 간 일치 여부는 다음 단계에서 판단한다.
    """
    result = []
    index = {}

    for candidate in candidates:
        qids = tuple(sorted({
            c["qid"]
            for c in candidate["qid_candidates"]
            if isinstance(c, dict) and is_qid(c.get("qid"))
        }))

        key = (
            candidate["candidate_origin"],
            candidate["relation_claim"],
            normalize(candidate["text"]),
            qids,
        )

        if key not in index:
            index[key] = len(result)
            result.append(candidate)
            continue

        existing = result[index[key]]

        for field in ("evidence_document_ids", "source_details"):
            for value in candidate[field]:
                if value not in existing[field]:
                    existing[field].append(value)

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-id", type=int, required=True)
    args = parser.parse_args()

    cid = args.content_id

    input_path = EXPLORATION / f"input_{cid}.json"
    inventory_path = EXPLORATION / f"inventory_{cid}.json"
    review_path = NORMALIZATION / f"keyword_qid_review_{cid}_v2.json"
    enriched_path = WIKIDATA / f"enriched_{cid}.json"

    output_path = NORMALIZATION / f"evidence_bundle_{cid}_v3.json"

    # 덮어쓰기를 허용하지 않는다.
    if output_path.exists():
        raise FileExistsError(f"이미 결과 파일이 존재함: {output_path}")

    input_data = load_json(input_path, required=True)
    inventory = load_json(inventory_path, required=True)
    review = load_json(review_path, required=True)
    enriched = load_json(enriched_path)

    for name, data in (
        ("input", input_data),
        ("inventory", inventory),
        ("review", review),
    ):
        if data.get("content_id") != cid:
            raise ValueError(f"{name}의 content_id가 일치하지 않음")

    documents = {}

    description = input_data.get("description")

    if isinstance(description, str) and description.strip():
        evidence_id = f"content_description:{cid}"

        documents[evidence_id] = {
            "evidence_id": evidence_id,
            "source": "exploration_input",
            "source_kind": "content_description",
            "section": None,
            "text": description,
            "source_url": None,
            "revision_id": None,
        }

    paragraphs = (
        input_data.get("wiki_paragraphs", [])
        + input_data.get("wiki_reference_paragraphs", [])
    )

    for paragraph in paragraphs:
        add_document(documents, paragraph)

    source_keywords = collect_source_keywords(inventory, review)
    wiki_themes = collect_wikipedia_themes(input_data, documents)
    wd_subjects = collect_wikidata_subjects(enriched)
    wiki_links = collect_wikipedia_links(review)

    candidates = deduplicate(
        source_keywords
        + wiki_themes
        + wd_subjects
        + wiki_links
    )

    warnings = []

    if not source_keywords:
        warnings.append("원본 키워드가 없음")

    if enriched is None:
        warnings.append("Wikidata enriched 파일이 없어 P921을 수집하지 못함")

    if not wiki_themes:
        warnings.append(
            "구조적으로 명시된 Wikipedia 주제 후보가 없음. "
            "주제가 없다는 의미는 아님"
        )

    output = {
        "schema_version": "evidence_bundle_v3",
        "content_id": cid,
        "content_type": input_data.get("content_type"),
        "title": input_data.get("title"),
        "release_year": input_data.get("release_year"),
        "genres": input_data.get("genres"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates,
        "evidence_documents": list(documents.values()),
        "summary": {
            "source_keywords": len(source_keywords),
            "wikipedia_explicit_themes": len(wiki_themes),
            "wikidata_subjects": len(wd_subjects),
            "wikipedia_links": len(wiki_links),
            "total_candidates_after_dedup": len(candidates),
            "evidence_documents": len(documents),
            "auto_approved": 0,
        },
        "input_files": {
            "exploration_input": str(input_path),
            "inventory": str(inventory_path),
            "qid_review_v2": str(review_path),
            "wikidata_enriched": (
                str(enriched_path) if enriched is not None else None
            ),
        },
        "warnings": warnings,
        "limitations": [
            "Wikipedia 주제 추출은 섹션명과 명시적 항목 구조에 한정됨",
            "Wikipedia 링크는 작품과의 관계가 검증되지 않음",
            "Wikidata P921은 외부 데이터의 주장으로 보존됨",
            "원본 키워드의 QID 후보는 의미 검증 전 상태임",
            "후보 출처 간 동의어 통합은 수행하지 않음",
            "QID 자동 선택 및 토픽 확정은 수행하지 않음",
            "DB, API, LLM을 사용하지 않음",
        ],
    }

    NORMALIZATION.mkdir(parents=True, exist_ok=True)

    with output_path.open("x", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n콘텐츠: {output['title']} ({cid})")
    print(f"원본 키워드: {len(source_keywords)}")
    print(f"Wikipedia 명시적 주제: {len(wiki_themes)}")
    print(f"Wikidata P921: {len(wd_subjects)}")
    print(f"Wikipedia 링크: {len(wiki_links)}")
    print(f"통합 후보: {len(candidates)}")
    print(f"근거 문서: {len(documents)}")
    print("자동 확정: 0")
    print(f"\n저장: {output_path}")


if __name__ == "__main__":
    main()