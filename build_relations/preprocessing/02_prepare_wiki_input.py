"""출처 분리 입력 생성. DB 쓰기·API·LLM 호출 없음."""
import argparse
import json
from pathlib import Path
from .source_input import build_source_input


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--base-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/enrichment/wiki_input_v2"))
    parser.add_argument("--allow-unlinked", action="store_true")
    parser.add_argument("--wiki-max-chars", type=int, default=1000)
    args = parser.parse_args()
    if args.wiki_max_chars < 1:
        parser.error("길이 제한은 양수여야 합니다.")
    context = json.loads(args.input.read_text(encoding="utf-8"))
    base = json.loads(args.base_json.read_text(encoding="utf-8"))
    payload, audit = build_source_input(context, base, args.allow_unlinked, args.wiki_max_chars)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cid = payload["content_id"]
    for name, value in (("input", payload), ("audit", audit)):
        path = args.output_dir / f"{name}_{cid}.json"
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"저장: {path.resolve()}")
    print(json.dumps(audit["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
