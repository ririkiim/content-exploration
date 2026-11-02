import argparse
import json
from pathlib import Path
from urllib.error import URLError

from .matching import match_content
from .repository import load_content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-id", type=int, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    print(f"[1/2] DB 작품 조회: {args.content_id}", flush=True)
    content = load_content(args.content_id)
    print(f"[2/2] Wikidata 조회: {content['title']}", flush=True)
    try:
        result = match_content(content)
    except (URLError, TimeoutError, RuntimeError) as error:
        result = {
            "status": "lookup_failed",
            "error": f"{type(error).__name__}: {error}",
        }

    payload = {"content": content, "result": result}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"결과 저장: {args.output.resolve()}")

    if result["status"] == "lookup_failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
