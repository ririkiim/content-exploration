import argparse
import json
import re
from pathlib import Path

from .enrichment import collect_wikidata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--qid", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"Q\d+", args.qid):
        parser.error("--qid는 Q 뒤에 숫자가 오는 형식이어야 합니다.")

    matched = json.loads(args.input.read_text(encoding="utf-8"))
    result = matched["result"]

    # 입력 JSON에 실제 후보로 존재하는 QID만 사용
    eligible_qids = result.get("eligible_qids", [])

    if args.qid not in eligible_qids:
        parser.error(
            f"입력 파일의 후보가 아닙니다. 후보: {eligible_qids}"
        )

    content = matched["content"]

    print(
        f"[Wikidata 수집] {content['title']} / {args.qid}",
        flush=True,
    )

    wikidata = collect_wikidata(args.qid)

    payload = {
        "content": content,
        "match": {
            "qid": args.qid,
            "method": result.get("method"),
            "selection": "user_confirmed",
        },
        "wikidata": wikidata,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"연결 정보: {len(wikidata['claims'])}개")
    print(f"한국어 Wikipedia: {wikidata['kowiki_title'] or '연결 없음'}")
    print(f"저장: {args.output.resolve()}")


if __name__ == "__main__":
    main()