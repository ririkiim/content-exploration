"""기존 30개 작품 일괄 점검·선택적 의미 확장. DB 쓰기 없음."""
import argparse
import contextlib
import importlib
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from .batch_targets import load_targets, resolve_targets


class Tee:
    def __init__(self, console, file):
        self.console, self.file = console, file
    def write(self, value):
        self.console.write(value)
        self.file.write(value)
        self.file.flush()
    def flush(self):
        self.console.flush()
        self.file.flush()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summarize(folder, records):
    report = {
        "total": len(records), "status_counts": dict(Counter(r["status"] for r in records)),
        "wiki_counts": dict(Counter(r.get("wiki_status", "not_checked") for r in records)),
        "total_tokens": sum(r.get("total_tokens") or 0 for r in records),
        "rows": records,
    }
    save(folder / "summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect-wiki", action="store_true", help="없는 위키 자료를 자동 매칭·수집")
    parser.add_argument("--content-id", nargs="+", type=int, help="생략하면 기존 30개 목록 사용")
    parser.add_argument("--run", action="store_true", help="준비 후 작품마다 LLM 한 번 호출. 생략하면 준비·검사만")
    parser.add_argument("--output-dir", type=Path, default=Path("data/enrichment/batch_runs"))
    parser.add_argument("--context-dir", type=Path, default=Path("data/enrichment/wikipedia"))
    parser.add_argument("--allow-unlinked", nargs="*", type=int, default=[])
    parser.add_argument("--model", help="생략하면 기존 설정 모델 사용")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--limit", type=int, help="앞 N개만 점검")
    args = parser.parse_args()
    if args.timeout <= 0 or (args.limit is not None and args.limit <= 0):
        parser.error("timeout·limit은 양수여야 합니다.")
    targets = load_targets()
    if args.limit:
        targets = targets[:args.limit]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    folder = args.output_dir / run_id
    folder.mkdir(parents=True, exist_ok=False)
    prepared_dir = folder / "prepared"
    prepared_dir.mkdir()
    prepare = importlib.import_module("build_relations.exploration.01_prepare")
    # 기존 링크 캐시는 파일로 재사용하며 새 네트워크 조회는 하지 않는다.
    cache_dir = Path("data/enrichment/exploration")
    with (folder / "run.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(Tee(sys.stdout, log)), contextlib.redirect_stderr(Tee(sys.stderr, log)):
            print(f"대상 {len(args.content_id) if args.content_id else len(targets)}개 / LLM 호출 {'활성' if args.run else '없음'}", flush=True)
            try:
                if args.content_id:
                    from .repository import load_catalogue
                    resolved = []
                    for cid in dict.fromkeys(args.content_id):
                        try:
                            c = load_catalogue(cid)["content"]
                            resolved.append({"content_id": cid, "requested_title": c["title"], "content_type": c["content_type"], "resolution": "matched", "candidate_ids": [cid]})
                        except Exception:
                            resolved.append({"content_id": None, "requested_title": str(cid), "content_type": "unknown", "resolution": "not_found", "candidate_ids": []})
                else:
                    resolved = resolve_targets(targets)
            except Exception as error:
                save(folder / "summary.json", {"status": "target_resolution_failed", "error": f"{type(error).__name__}: {error}"})
                raise
            save(folder / "targets.json", resolved)
            records = []
            with (folder / "progress.jsonl").open("w", encoding="utf-8") as progress:
                for index, target in enumerate(resolved, 1):
                    started = time.monotonic()
                    row = {**target}
                    cid = target["content_id"]
                    print(f"\n[{index}/{len(resolved)}] {cid} | {target['requested_title']}", flush=True)
                    try:
                        if target["resolution"] != "matched":
                            row["status"] = "target_" + target["resolution"]
                        else:
                            if args.collect_wiki:
                                from .auto_wiki import ensure_context
                                row["wiki_collection"] = ensure_context(cid, args.context_dir)
                                print("위키 수집:", row["wiki_collection"]["status"], flush=True)
                            cached = cache_dir / f"links_{cid}.json"
                            if cached.exists():
                                (prepared_dir / cached.name).write_text(cached.read_text(encoding="utf-8"), encoding="utf-8")
                            options = SimpleNamespace(context_dir=args.context_dir, output_dir=prepared_dir,
                                refresh_links=False, allow_unlinked=args.allow_unlinked, wiki_max_chars=1000)
                            prepare.prepare_one(cid, options)
                            content = json.loads((prepared_dir / f"input_{cid}.json").read_text(encoding="utf-8"))
                            audit = json.loads((prepared_dir / f"audit_{cid}.json").read_text(encoding="utf-8"))
                            row.update(
                                wiki_status="included" if content["wiki_paragraphs"] else "not_used",
                                wiki_reason=audit.get("wiki_input_status", audit.get("wikipedia_status", "selected")),
                                description_chars=len(content.get("description") or ""),
                                wiki_chars=sum(len(p["text"]) for p in content["wiki_paragraphs"]),
                                known_items=len(content["known_items"]),
                            )
                            row["context_warning"] = (
                                "short_catalogue_description_without_wiki"
                                if len(content.get("description") or "") < 150 and not content["wiki_paragraphs"] else None
                            )
                            if not content.get("description", "").strip() and not content["wiki_paragraphs"]:
                                row["status"] = "insufficient_context"
                                print("의미 추출 원문 없음. LLM 호출을 생략합니다.")
                            else:
                                command = [sys.executable, "-B", "-m", "build_relations.LLM.run_wiki_topics",
                                           "--content-id", str(cid), "--input-dir", str(prepared_dir),
                                           "--output-dir", str(folder / "results")]
                                if args.run:
                                    command.append("--run")
                                if args.model:
                                    command += ["--model", args.model]
                                completed = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
                                print(completed.stdout, end="")
                                if completed.stderr:
                                    print(completed.stderr, end="", file=sys.stderr)
                                if completed.returncode:
                                    row.update(status="llm_failed" if args.run else "input_check_failed", returncode=completed.returncode)
                                else:
                                    row["status"] = "completed" if args.run else "input_ready"
                                results = list((folder / "results" / str(cid)).glob("*/result.json"))
                                if results:
                                    result_path = max(results, key=lambda p: p.stat().st_mtime_ns)
                                    result = json.loads(result_path.read_text(encoding="utf-8"))
                                    if result.get("status") == "success":
                                        if not result.get("topics"):
                                            row["status"] = "completed_empty"
                                        elif not result.get("semantic_candidates"):
                                            row["status"] = "completed_held_only"
                                    for topic in result.get("topics", []):
                                        print(f"  {'후보' if topic['eligible_for_review'] else '보류'}: {topic['name']} [{topic['type']}]")
                                        print(f"    출처: {topic.get('declared_evidence_source')}")
                                        print(f"    인용: {topic['evidence_quote']}")
                                        print(f"    설명: {topic['evidence']}")
                                        print(f"    검토: {topic.get('review_flags', [])}")
                                    row.update(result_path=str(result_path.resolve()), summary=result.get("summary"),
                                               total_tokens=(result.get("token_usage") or {}).get("total_tokens"))
                    except subprocess.TimeoutExpired:
                        row.update(status="timeout", error=f"작품별 {args.timeout}초 제한 초과")
                    except Exception as error:
                        row.update(status="prepare_failed", error=f"{type(error).__name__}: {error}")
                    row["elapsed_seconds"] = round(time.monotonic() - started, 2)
                    records.append(row)
                    progress.write(json.dumps(row, ensure_ascii=False) + "\n")
                    progress.flush()
                    summarize(folder, records)
                    print(f"상태: {row['status']} / {row['elapsed_seconds']}초", flush=True)
            report = summarize(folder, records)
            print("\n전체 상태:", report["status_counts"])
            print("위키 상태:", report["wiki_counts"])
            print(f"로그: {(folder / 'run.log').resolve()}")
            print(f"요약: {(folder / 'summary.json').resolve()}")
    if any(r["status"] in {"prepare_failed", "llm_failed", "input_check_failed", "timeout"} or r["status"].startswith("target_") for r in records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
