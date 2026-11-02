import argparse
import importlib.util
import json
from pathlib import Path

BASE = Path("data/enrichment/normalization_v2")
SCRIPT = Path("build_relations/normalization/05_match_source_keywords.py")


def load_matcher():
    spec = importlib.util.spec_from_file_location("qid_matcher", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-id", type=int, required=True)
    args = parser.parse_args()

    cid = args.content_id
    source = BASE / f"evidence_bundle_{cid}_v4.json"
    target = BASE / f"evidence_bundle_{cid}_v4_qid.json"

    if target.exists():
        print(f"기존 결과 존재: {target}")
        return

    data = json.loads(source.read_text(encoding="utf-8"))
    matcher = load_matcher()

    topics = [
        c for c in data["candidates"]
        if c.get("candidate_origin") == "llm_semantic_expansion"
    ]

    search_results = {}
    for topic in topics:
        name = topic["text"]
        print("검색:", name)
        search_results[name] = matcher.search_keyword(name)

    all_qids = {
        qid
        for qids in search_results.values()
        for qid in qids
    }
    entities = matcher.fetch_entities(all_qids)

    for topic in topics:
        name = topic["text"]
        qids = search_results[name]

        candidates = matcher.matching_candidates(
            name,
            {
                qid: entities[qid]
                for qid in qids
                if qid in entities
            },
        )

        topic["qid_candidates"] = candidates
        topic["selected_qid"] = None

        print(f"  QID 후보: {len(candidates)}개")

    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("저장:", target)


if __name__ == "__main__":
    main()