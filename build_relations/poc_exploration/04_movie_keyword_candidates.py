"""영화 KMDb 키워드 → QID 후보. 기존 입력·결과·DB를 덮어쓰지 않는다."""
import argparse
import json
from pathlib import Path
from .repository import load_catalogue
from .movie_keywords import collect_movie_keywords, resolve_keyword
from .qid_store import EntityStore
from .identity import normalize
from .extract import collect_inventory, add_link_identification


def main():
    parser=argparse.ArgumentParser()
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--content-id",type=int)
    source.add_argument("--catalogue-snapshot",type=Path)
    parser.add_argument("--context",type=Path,help="이미 수집한 Wikidata/Wikipedia 보완 자료")
    parser.add_argument("--links",type=Path,help="이미 수집한 Wikipedia 링크 후보")
    parser.add_argument("--collect-supplement",action="store_true",help="키워드가 없거나 식별이 부족하면 기존 위키 수집 모듈로 보완")
    parser.add_argument("--keyword",nargs="+",help="이 키워드들만 매칭 시도. 다른 원본 키워드도 전부 보존")
    parser.add_argument("--cache-dir",type=Path,default=Path("data/enrichment/qid_cache"))
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error("기존 결과 보호: 새 output 경로를 지정하세요.")
    bundle=json.loads(args.catalogue_snapshot.read_text(encoding="utf-8")) if args.catalogue_snapshot else load_catalogue(args.content_id)
    result=collect_movie_keywords(bundle)
    cid=result["content_id"]
    context_path=args.context or Path(f"data/enrichment/wikipedia/context_{cid}.json")
    context=json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else {}
    if context and context.get("content",{}).get("content_id")!=cid:parser.error("문맥 자료의 content_id 불일치")
    store=EntityStore(args.cache_dir)
    selected={normalize(n) for n in args.keyword} if args.keyword else None
    if selected and not selected.issubset({normalize(k["name"]) for k in result["keywords"]}):parser.error("KMDb 목록에 없는 keyword입니다.")
    for i,k in enumerate(result["keywords"]):
        if selected is None or normalize(k["name"]) in selected:
            print("식별 시도:",k["name"],flush=True)
            try:
                result["keywords"][i]=resolve_keyword(k,store,context)
            except Exception as error:
                result["keywords"][i]={**k,"match_status":"lookup_failed","error":f"{type(error).__name__}: {error}"}
            print(result["keywords"][i]["match_status"],flush=True)
    unresolved=[k["name"] for k in result["keywords"] if not k.get("qid")]
    result["supplement_reason"]="no_kmdb_keywords" if not result["keywords"] else "unresolved_keywords" if unresolved else None
    if result["supplement_reason"] and args.collect_supplement:
        from .auto_wiki import ensure_context
        context_dir=Path("data/enrichment/wikipedia")
        result["supplement_collection"]=ensure_context(cid,context_dir)
        path=context_dir/f"context_{cid}.json"
        if path.exists():
            context=json.loads(path.read_text(encoding="utf-8"))
            context_path=path
        # 새 문맥에서 구조화 연결을 얻은 키워드만 재검토한다. 검색 캐시는 재사용한다.
        for i,k in enumerate(result["keywords"]):
            if k["match_status"] != "not_attempted" and not k.get("qid"):
                try:
                    result["keywords"][i]=resolve_keyword(k,store,context)
                except Exception as error:
                    result["keywords"][i]={**k,"match_status":"lookup_failed","error":f"{type(error).__name__}: {error}"}
        unresolved=[k["name"] for k in result["keywords"] if not k.get("qid")]
    if context:
        inventory=collect_inventory(bundle,context)
        links=json.loads(args.links.read_text(encoding="utf-8")) if args.links else {"candidates":[]}
        if args.collect_supplement and context.get("wikipedia",{}).get("status")=="success" and not args.links:
            from .wiki_links import collect_links
            try:
                links=collect_links(context["wikipedia"])
            except Exception as error:
                result["supplement_link_error"]=f"{type(error).__name__}: {error}"
        if args.links and (links.get("source_page_id"),links.get("source_revision_id")) != (context.get("wikipedia",{}).get("page_id"),context.get("wikipedia",{}).get("revision_id")):
            parser.error("Wikipedia 링크와 문맥 버전 불일치")
        inventory=add_link_identification(inventory,links.get("candidates",[]))
        result["supplementary_candidates"]=[a for a in inventory["entity_candidates"] if a["identification_method"]=="wikipedia_link_and_type" or any(e.get("source")=="wikidata" for e in a["evidence"])]
        result["supplement_context_path"]=str(context_path.resolve())
    result["unresolved_keywords"]=unresolved
    result["cache_stats"]=store.stats
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2,default=str)
    print("저장:",args.output.resolve())
    for k in result["keywords"]:print(k["name"],k["match_status"],k.get("qid") or k.get("qid_candidate"),k["kind"])

if __name__=="__main__":main()
