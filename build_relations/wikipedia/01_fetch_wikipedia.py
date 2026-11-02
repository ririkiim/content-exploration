import argparse
import json
from pathlib import Path
from urllib.error import URLError

from .wikipedia_client import fetch_wikipedia


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--title",
        help="직접 확인한 Wikipedia 문서 제목",
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    title = args.title or payload["wikidata"].get("kowiki_title")
    expected_qid = payload["wikidata"]["qid"]

    print(f"[Wikipedia 수집] {title or '연결 없음'}", flush=True)

    try:
        wikipedia = fetch_wikipedia(title, expected_qid=expected_qid)
    except (URLError, TimeoutError, RuntimeError) as error:
        wikipedia = {
            "status": "lookup_failed",
            "requested_title": title,
            "error": f"{type(error).__name__}: {error}",
        }

    wikipedia["selection_method"] = (
        "manual_title" if args.title else "wikidata_sitelink"
    )
    wikipedia["identity_review_required"] = (
        wikipedia.get("identity_check") == "unlinked"
    )
    payload["wikipedia"] = wikipedia

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"상태: {wikipedia['status']}")
    print(f"본문 길이: {len(wikipedia.get('text', ''))}자")
    print(f"저장: {args.output.resolve()}")

    if wikipedia["identity_review_required"]:
        print("주의: 문서의 QID 연결이 없어 작품 유형·저자 확인이 필요합니다.")

    if wikipedia["status"] in {
        "lookup_failed", "qid_mismatch", "not_found", "disambiguation", "empty"
    }:
        raise SystemExit(1)


if __name__ == "__main__":
    main()