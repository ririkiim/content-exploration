import argparse
import copy
import json
from pathlib import Path

BASE = Path("data/enrichment")
NORMALIZATION = BASE / "normalization_v2"
SEMANTIC = BASE / "semantic_topics"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-id", type=int, required=True)
    parser.add_argument("--llm-result", type=Path)
    args = parser.parse_args()

    cid = args.content_id

    source = NORMALIZATION / f"evidence_bundle_{cid}_v3.json"
    target = NORMALIZATION / f"evidence_bundle_{cid}_v4.json"

    if target.exists():
        print(f"기존 v4 재사용: {target}")
        return

    if args.llm_result:
        llm_path = args.llm_result
    else:
        results = sorted((SEMANTIC / str(cid)).glob("*/result.json"))
        if len(results) != 1:
            raise ValueError(
                f"LLM 결과가 {len(results)}개입니다. "
                "--llm-result로 사용할 파일을 지정하세요."
            )
        llm_path = results[0]

    bundle = read_json(source)
    llm = read_json(llm_path)

    if bundle.get("content_id") != cid:
        raise ValueError("Evidence Bundle의 content_id 불일치")

    if llm.get("content_id") != cid:
        raise ValueError("LLM 결과의 content_id 불일치")

    if llm.get("status") != "success":
        raise ValueError("LLM 추출이 성공한 결과가 아닙니다.")

    if bundle.get("schema_version") != "evidence_bundle_v3":
        raise ValueError("지원하지 않는 Evidence Bundle 버전")

    topics = llm.get("topics")
    if not isinstance(topics, list):
        raise ValueError("LLM 결과에 topics 목록이 없습니다.")

    merged = copy.deepcopy(bundle)
    merged["schema_version"] = "evidence_bundle_v4"

    existing = {
        (c.get("candidate_origin"), c.get("text"))
        for c in merged["candidates"]
    }

    added = 0

    for topic in topics:
        name = topic.get("name")

        if not name:
            raise ValueError("이름이 없는 LLM 토픽이 있습니다.")

        if topic.get("quote_valid") is not True:
            raise ValueError(f"근거 검증 실패: {name}")

        if not topic.get("evidence_quote"):
            raise ValueError(f"근거 문장 누락: {name}")

        key = ("llm_semantic_expansion", name)

        if key in existing:
            continue

        detail = dict(topic)
        detail.update({
            "source": "llm",
            "model": llm.get("model"),
            "prompt_version": llm.get("prompt_version"),
            "generation_path": "llm_semantic_expansion",
        })

        merged["candidates"].append({
            "text": name,
            "candidate_origin": "llm_semantic_expansion",
            "relation_claim": "semantic_topic",
            "qid_candidates": [],
            "evidence_document_ids": [],
            "source_details": [detail],
            "selected_qid": None,
            "review_status": "pending",
        })

        existing.add(key)
        added += 1

    merged["summary"] = {
        **merged.get("summary", {}),
        "llm_semantic_topics_added": added,
        "total_candidates_v4": len(merged["candidates"]),
    }

    target.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"콘텐츠 ID: {cid}")
    print(f"기존 후보: {len(bundle['candidates'])}개")
    print(f"LLM 추가: {added}개")
    print(f"최종 후보: {len(merged['candidates'])}개")
    print(f"저장: {target}")


if __name__ == "__main__":
    main()