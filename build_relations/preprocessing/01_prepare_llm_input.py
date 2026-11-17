"""세 작품 실험용 전처리. API·LLM 호출이나 DB 쓰기는 하지 않는다."""
import argparse
import json
from pathlib import Path
from .wiki_filter import build_inputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-json", type=Path, help="생략하면 DB에서 기본 설명·키워드 조회")
    parser.add_argument("--allow-unlinked", action="store_true")
    parser.add_argument("--max-chars", type=int, default=6000)
    parser.add_argument("--max-paragraph-chars", type=int, default=2000)
    parser.add_argument("--compact", action="store_true", help="영화·TV 위키 문장만 압축. 도서는 유지")
    parser.add_argument("--wiki-max-chars", type=int, default=650)
    parser.add_argument("--max-wiki-sentences", type=int, default=5)
    args = parser.parse_args()
    if min(args.max_chars, args.max_paragraph_chars, args.wiki_max_chars, args.max_wiki_sentences) <= 0:
        parser.error("길이 제한은 양수여야 합니다.")
    context = json.loads(args.input.read_text(encoding="utf-8"))
    cid = context["content"]["content_id"]
    if args.base_json:
        base = json.loads(args.base_json.read_text(encoding="utf-8"))
    else:
        from .repository import load_base
        base = load_base(cid)
    results = build_inputs(
        context, base, args.max_chars, args.max_paragraph_chars, args.allow_unlinked,
    )
    if args.compact:
        from .compact import compact_inputs
        results = compact_inputs(results, args.wiki_max_chars, args.max_wiki_sentences)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for kind, value in results.items():
        path = args.output_dir / f"{kind}_{cid}.json"
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"저장: {path.resolve()}")
    print(json.dumps(results["audit"]["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
