"""출처 분리 입력으로 보강 결과만 한 번 추출. 기본은 검사만 수행."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_content_topics import get_client, MODEL_NAME
from wiki_prompt import WIKI_SYSTEM_PROMPT, PROMPT_VERSION, prepare_wiki_input, build_wiki_prompt
from wiki_evidence import validate_wiki_response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-id", type=int, required=True)
    parser.add_argument("--input-dir", type=Path, default=Path("data/enrichment/exploration"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/enrichment/semantic_topics"))
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--run", action="store_true", help="LLM을 한 번 호출하고 결과 저장")
    args = parser.parse_args()
    cid = args.content_id
    content = json.loads((args.input_dir / f"input_{cid}.json").read_text(encoding="utf-8"))
    audit = json.loads((args.input_dir / f"audit_{cid}.json").read_text(encoding="utf-8"))
    prepared = prepare_wiki_input(content)
    if int(prepared["content_id"]) != cid or audit["content_id"] != cid:
        parser.error("입력과 audit의 작품 ID가 다릅니다.")
    audit_text = {s["source_id"]: s["text"] for s in audit["sources"]}
    for p in prepared["wiki_paragraphs"]:
        if audit_text.get(p["source_id"]) != p["text"]:
            parser.error("위키 문단과 audit 원문이 다릅니다.")
    print(f"DB 설명 {len(prepared['description'])}자 / 확보 항목 {len(prepared['known_items'])}개")
    print(f"위키 {len(prepared['wiki_paragraphs'])}개 구간 / {sum(len(p['text']) for p in prepared['wiki_paragraphs'])}자")
    if not args.run:
        print("입력 검사 완료. API 호출 없음. 실행하려면 --run을 추가하세요.")
        return
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    folder = args.output_dir / str(cid) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "content_id": cid, "model": args.model, "prompt_version": PROMPT_VERSION,
        "system_prompt": WIKI_SYSTEM_PROMPT,
        "prompt_sha256": hashlib.sha256(WIKI_SYSTEM_PROMPT.encode()).hexdigest(),
        "input_snapshot": prepared, "candidate_status": "proposed",
    }
    started = time.monotonic()
    try:
        request = {
            "model": args.model, "messages": [
                {"role": "system", "content": WIKI_SYSTEM_PROMPT},
                {"role": "user", "content": build_wiki_prompt(content)},
            ], "response_format": {"type": "json_object"},
        }
        if not args.model.startswith("gpt-5.6"):
            request["temperature"] = 0.0
        response = get_client().chat.completions.create(**request)
        result["token_usage"] = response.usage.model_dump() if response.usage else None
        raw = response.choices[0].message.content
        result["raw_response"] = raw
        if not raw:
            raise ValueError("빈 LLM 응답")
        topics = validate_wiki_response(raw, content)
        result.update(status="success", topics=topics,
                      semantic_candidates=[t for t in topics if t["eligible_for_review"]],
                      held_candidates=[t for t in topics if not t["eligible_for_review"]])
        result["summary"] = {
            "topics": len(topics), "semantic_candidates": sum(t["eligible_for_review"] for t in topics), "held_candidates": sum(not t["eligible_for_review"] for t in topics), "exact_quote_matches": sum(t["exact_quote"] for t in topics),
            "wiki_evidence": sum(t["wiki_evidence"] for t in topics),
            "wiki_only_quote": sum(t["wiki_only_quote"] for t in topics),
            "note": "wiki_only_quote는 같은 인용의 출처 범위이며 신규 토픽 수가 아닙니다.",
        }
    except Exception as error:
        result.update(status="failed", error=f"{type(error).__name__}: {error}")
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    path = folder / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {path.resolve()}")
    if result["status"] == "failed":
        raise SystemExit(result["error"])


if __name__ == "__main__":
    main()
