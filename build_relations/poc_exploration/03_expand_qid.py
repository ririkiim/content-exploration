"""QID를 선택해 한 단계씩 확장하는 독립 CLI. DB 스키마 변경 없음."""
import argparse
import json
from pathlib import Path
from .qid_store import EntityStore, check_qid
from .qid_expansion import expand_one, new_session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qid", nargs="+", required=True, type=check_qid)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/enrichment/qid_cache"))
    parser.add_argument("--max-edges", type=int, default=60)
    args = parser.parse_args()
    if args.max_edges < 1:
        parser.error("max-edges는 양수여야 합니다.")
    session = json.loads(args.session.read_text(encoding="utf-8")) if args.session.exists() else new_session()
    if session.get("schema_version") != "qid-expansion-v1":
        parser.error("알 수 없는 세션 형식입니다.")
    store = EntityStore(args.cache_dir)
    args.session.parent.mkdir(parents=True, exist_ok=True)
    for qid in args.qid:
        step = expand_one(qid, store, session, args.max_edges)
        session["last_call_cache_stats"] = store.stats
        # 세션은 진행 상태. API 원본 캐시는 덮어쓰지 않는다.
        temporary = args.session.with_suffix(args.session.suffix + ".tmp")
        temporary.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(args.session)
        print(json.dumps(step, ensure_ascii=False))
        for edge in session["edges"].values():
            if edge["source_qid"] == qid:
                target = session["nodes"][edge["target_qid"]]
                print(f"  {edge['property_id']} {edge['relation']} → {target['name']} ({target['qid']}) / 출처 있음 {sum(e['has_reference'] for e in edge['evidence'])}/{len(edge['evidence'])}")
    print("캐시:", store.stats)
    print("세션:", args.session.resolve())

if __name__ == "__main__":
    main()
