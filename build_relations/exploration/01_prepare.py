"""정보 확보·문맥 선택·의미 확장 입력 생성. LLM 호출과 DB 쓰기 없음."""
import argparse
import json
from pathlib import Path
from .repository import load_catalogue, match_target_content
from .extract import collect_inventory, known_items, add_link_identification
from .wiki_links import collect_links
from ..preprocessing.source_input import build_source_input


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def prepare_one(cid, args):
    context_path = args.context_dir / f"context_{cid}.json"
    bundle = load_catalogue(cid)
    if context_path.exists():
        context = json.loads(context_path.read_text(encoding="utf-8"))
    else:
        context = {
            "content": bundle["content"], "wikidata": {},
            "wikipedia": {"status": "not_collected", "reason": "저장된 위키 자료 없음. DB 입력으로 진행."},
        }
    if context["content"]["content_id"] != cid:
        raise ValueError("위키 자료의 content_id가 다릅니다.")
    inventory = collect_inventory(bundle, context)
    detail, ctype = bundle["detail"], bundle["content"]["content_type"]
    base = {**bundle["content"],
            "description": detail.get("description") if ctype == "book" else detail.get("overview"),
            "keywords": detail.get("keywords") or [], "genres": detail.get("genres") or [],
            "description_source": detail.get("description_source"),
    }
    source_urls = {s["source_name"]: s.get("source_url") for s in bundle["sources"]}
    inventory["catalogue_sources"] = bundle["sources"]
    link_path = args.output_dir / f"links_{cid}.json"
    wp = context.get("wikipedia", {})
    if args.refresh_links and wp.get("status") == "success":
        links = collect_links(wp)
        for candidate in links["candidates"]:
            if candidate["candidate_kind"] == "work":
                ids = match_target_content(candidate.get("target_qid"), candidate.get("external_ids"))
                candidate["catalogue_content_ids"] = ids
                candidate["catalogue_match_status"] = "matched" if len(ids) == 1 else "ambiguous" if ids else "no_match"
        save(link_path, links)
    elif link_path.exists():
        links = json.loads(link_path.read_text(encoding="utf-8"))
        if (links.get("source_page_id"), links.get("source_revision_id")) != (wp.get("page_id"), wp.get("revision_id")):
            raise ValueError("링크 캐시의 문서/버전이 다릅니다. --refresh-links로 갱신하세요.")
    else:
        links = {"status": "not_collected", "candidates": [], "maintenance_flags": [],
                 "note": "본문 텍스트에서 링크를 추측하지 않음. --refresh-links로 수집."}
    inventory = add_link_identification(inventory, links.get("candidates", []))
    from .auto_wiki import validate_page
    identity_content = {**bundle["content"], "authors": [
        row["name"] for row in detail.get("contributors", [])
        if row.get("role") == "author" and row.get("name")
    ]}
    decision = validate_page(identity_content, context.get("wikidata", {}), wp)
    allow = decision["accepted"]
    if wp.get("status") == "success" and not allow:
        context = {**context, "wikipedia": {**wp, "collection_status": "success", "status": "identity_unconfirmed"}}
        wp = context["wikipedia"]
    if wp.get("status") == "success":
        payload, audit = build_source_input(context, base, allow_unlinked=allow, wiki_max_chars=args.wiki_max_chars)
    else:
        # 위키가 없어도 DB 기반 의미 확장은 가능하다.
        from ..preprocessing.source_input import clean_book_description
        cleaned, decisions = clean_book_description(base)
        payload = {**cleaned, "wiki_paragraphs": []}
        audit = {"content_id": cid, "sources": [], "catalogue_sentence_decisions": decisions,
                 "wikipedia_status": wp.get("status", "not_collected")}
    audit["identity_decision"] = decision
    audit["wiki_link_status"] = links["status"]
    audit["maintenance_flags"] = links.get("maintenance_flags", [])
    audit["maintenance_check"] = "templates_checked" if links["status"] == "success" else "not_checked"
    if audit["maintenance_flags"]:
        audit["held_wiki_paragraphs"] = payload["wiki_paragraphs"]
        payload["wiki_paragraphs"] = []
        audit["wiki_input_status"] = "held_for_maintenance_review"
    from .known_selection import select_known_items
    payload["known_items"], audit["known_items_selection"] = select_known_items(
        inventory, links.get("candidates", []), context,
    )
    # 원본 키워드는 inventory에 전량 보존. 의미 확장 LLM에는 재추출 근거로 전달하지 않는다.
    payload["keywords"] = []
    payload["task"] = "semantic_expansion_only"
    payload["source_schema_version"] = "code-first-v1"
    audit["raw_keywords_location"] = f"inventory_{cid}.json:source_tags"
    audit["context_file"] = str(context_path.resolve())
    audit["input_policy"] = "source facts and tags retained separately; no keyword re-extraction"
    save(args.output_dir / f"inventory_{cid}.json", inventory)
    save(args.output_dir / f"input_{cid}.json", payload)
    save(args.output_dir / f"audit_{cid}.json", audit)
    save(args.output_dir / f"catalogue_{cid}.json", bundle)
    print(f"{cid}: 출처 관계 {len(inventory['assertions'])}개 / 태그 {len(inventory['source_tags'])}개 / "
          f"위키 구간 {len(payload['wiki_paragraphs'])}개 / 알려진 항목 {len(payload['known_items'])}개")
    print(f"링크: {links['status']} / 후보 {len(links.get('candidates', []))}개")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-id", nargs="+", required=True, type=int)
    parser.add_argument("--context-dir", type=Path, default=Path("data/enrichment/wikipedia"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/enrichment/exploration_v2"))
    parser.add_argument("--allow-unlinked", nargs="*", type=int, default=[])
    parser.add_argument("--refresh-links", action="store_true", help="Wikipedia/Wikidata API 읽기. LLM 호출 없음")
    parser.add_argument("--wiki-max-chars", type=int, default=None)
    args = parser.parse_args()
    if args.wiki_max_chars is not None and args.wiki_max_chars <= 0:
        parser.error("길이는 양수여야 합니다.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    for cid in args.content_id:
        try:
            prepare_one(cid, args)
        except Exception as error:
            failures.append(cid)
            save(args.output_dir / f"error_{cid}.json", {"content_id": cid, "error": f"{type(error).__name__}: {error}"})
            print(f"실패 {cid}: {type(error).__name__}: {error}")
    if failures:
        raise SystemExit(f"실패한 작품: {failures}")


if __name__ == "__main__":
    main()
