"""DB 키워드 우선 Wikidata QID 식별 v2.

1. Wikipedia 링크 QID를 일괄 조회
2. DB 키워드와 위키 QID의 라벨/별칭 비교
3. 미매칭 DB 키워드만 Wikidata 검색
4. 검색 후보 상세정보 일괄 조회
5. 기존 데이터 수정 없이 별도 JSON 저장

DB 쓰기 및 LLM 호출 없음.
"""

import hashlib
import argparse
import json
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API = "https://www.wikidata.org/w/api.php"

INPUT_DIR = Path("data/enrichment/exploration_v2")
OUTPUT_DIR = Path("data/enrichment/normalization_v2")
CACHE_DIR = OUTPUT_DIR / "wikidata_cache"

USER_AGENT = "ContentExplorationResearch/0.2 (academic entity matching)"


def normalize(value):
    return unicodedata.normalize(
        "NFC", str(value or "")
    ).strip().casefold()


def request_json(params):
    url = API + "?" + urlencode(params)

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    for attempt in range(5):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)

        except HTTPError as error:
            if error.code == 429:
                retry_after = error.headers.get("Retry-After")

                try:
                    wait = max(10, float(retry_after))
                except (TypeError, ValueError):
                    wait = min(30 * (2 ** attempt), 300)

                if attempt == 4:
                    raise

                print(f"429 요청 제한: {wait:.0f}초 대기")
                time.sleep(wait)
                continue

            if error.code in {500, 502, 503, 504} and attempt < 4:
                time.sleep(min(5 * (2 ** attempt), 60))
                continue

            raise

        except (URLError, TimeoutError):
            if attempt == 4:
                raise

            time.sleep(min(5 * (2 ** attempt), 60))


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_new_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("x", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def cached_request(cache_path, params):
    """동일 요청의 성공 결과를 재사용한다."""
    if cache_path.exists():
        return load_json(cache_path)

    data = request_json(params)
    save_new_json(cache_path, data)
    return data


def fetch_entities(qids):
    """QID 상세정보를 최대 40개씩 일괄 조회."""
    qids = sorted(set(qids))
    entities = {}

    for start in range(0, len(qids), 40):
        batch = qids[start:start + 40]

        cache_key = hashlib.sha256(
            "|".join(batch).encode("utf-8")
        ).hexdigest()[:16]

        cache_path = CACHE_DIR / f"entities_p31_{cache_key}.json"

        data = cached_request(
            cache_path,
            {
                "action": "wbgetentities",
                "format": "json",
                "ids": "|".join(batch),
                "props": "labels|aliases|descriptions|claims",
                "languages": "ko|en",
            },
        )

        entities.update(data.get("entities", {}))

        time.sleep(1)

    return entities

def fetch_p31_labels(qids):
    """P31 유형 QID의 한국어·영어 라벨을 일괄 조회한다."""
    qids = sorted(set(qids))

    if not qids:
        return {}

    result = {}

    for start in range(0, len(qids), 40):
        batch = qids[start:start + 40]

        cache_key = hashlib.sha256(
            "|".join(batch).encode("utf-8")
        ).hexdigest()[:16]

        cache_path = CACHE_DIR / f"p31_labels_{cache_key}.json"

        data = cached_request(
            cache_path,
            {
                "action": "wbgetentities",
                "format": "json",
                "ids": "|".join(batch),
                "props": "labels",
                "languages": "ko|en",
            },
        )

        for qid, entity in data.get("entities", {}).items():
            labels = entity.get("labels", {})

            result[qid] = {
                "qid": qid,
                "ko": labels.get("ko", {}).get("value"),
                "en": labels.get("en", {}).get("value"),
            }

    return result

def search_keyword(keyword):
    language = (
        "ko"
        if any("\uac00" <= c <= "\ud7a3" for c in keyword)
        else "en"
    )

    # 키워드별 검색 결과 캐시
    safe_name = keyword.encode("utf-8").hex()

    cache_path = CACHE_DIR / f"search_{safe_name}.json"

    data = cached_request(
        cache_path,
        {
            "action": "wbsearchentities",
            "format": "json",
            "search": keyword,
            "language": language,
            "uselang": language,
            "type": "item",
            "limit": 10,
        },
    )

    return [
        item["id"]
        for item in data.get("search", [])
        if item.get("id", "").startswith("Q")
    ]


def values(entity, field):
    result = []

    for language in ("ko", "en"):
        item = entity.get(field, {}).get(language)

        if isinstance(item, list):
            result.extend(
                value["value"]
                for value in item
                if value.get("value")
            )

        elif isinstance(item, dict) and item.get("value"):
            result.append(item["value"])

    return result


def describe(qid, entity, keyword):
    labels = values(entity, "labels")
    aliases = values(entity, "aliases")
    descriptions = values(entity, "descriptions")

    name = normalize(keyword)

    label_match = any(normalize(v) == name for v in labels)
    alias_match = any(normalize(v) == name for v in aliases)

    return {
        "qid": qid,
        "labels": labels,
        "aliases": aliases,
        "descriptions": descriptions,
        "instance_of_qids": extract_p31(entity),
        "exact_label": label_match,
        "exact_alias": alias_match,
    }

def extract_p31(entity):
    """Wikidata 엔티티의 직접적인 P31 QID를 추출한다."""
    qids = []

    for statement in entity.get("claims", {}).get("P31", []):
        if statement.get("rank") == "deprecated":
            continue

        mainsnak = statement.get("mainsnak", {})

        if mainsnak.get("snaktype") != "value":
            continue

        value = mainsnak.get("datavalue", {}).get("value", {})

        if not isinstance(value, dict):
            continue

        qid = value.get("id")

        if qid and qid not in qids:
            qids.append(qid)

    return qids

def matching_candidates(keyword, entities):
    candidates = []

    for qid, entity in entities.items():
        item = describe(qid, entity, keyword)

        if item["exact_label"] or item["exact_alias"]:
            candidates.append(item)

    return candidates


def build_wiki_index(links):
    index = {}

    for link in links.get("candidates", []):
        qid = link.get("target_qid")

        if not qid:
            continue

        index.setdefault(qid, []).append({
            "label": link.get("label"),
            "canonical_title": link.get("canonical_title"),
            "section": link.get("section"),
            "candidate_kind": link.get("candidate_kind"),
            "evidence": link.get("evidence"),
        })

    return index


def identify_from_wikipedia(tag, wiki_entities, wiki_index):
    keyword = tag["name"]

    matches = matching_candidates(keyword, wiki_entities)

    # 같은 이름의 위키 링크가 여러 QID에 연결된다면 보류
    if len(matches) == 1:
        item = matches[0]

        return {
            "status": "matched",
            "matched_qid": item["qid"],
            "reason": "exact_name_in_existing_wikipedia_link",
            "candidates": matches,
            "wikipedia_evidence": wiki_index[item["qid"]],
        }

    if len(matches) > 1:
        return {
            "status": "review",
            "matched_qid": None,
            "reason": "multiple_matching_wikipedia_qids",
            "candidates": matches,
            "wikipedia_evidence": [],
        }

    return None


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--content-id", type=int, required=True)

    args = parser.parse_args()
    cid = args.content_id

    

    inventory = load_json(
        INPUT_DIR / f"inventory_{cid}.json"
    )

    links_path = INPUT_DIR / f"links_{cid}.json"

    if links_path.exists():
        links = load_json(links_path)
    else:
        print(f"[WARN] {cid}: Wikipedia 링크 없음")
        links = {
            "content_id": cid,
            "status": "not_collected",
            "candidates": [],
        }

    if links.get("status") != "success":
        print(
            f"[WARN] {cid}: Wikipedia 링크 수집 결과 없음. "
            "DB 원본 키워드만으로 QID 검색을 진행합니다."
        )
        links["candidates"] = []

    # 장르는 별도로 보존하되, 이번 QID 검색 대상에서는 제외
    source_tags = inventory.get("source_tags", [])

    keywords = [
        tag for tag in source_tags
        if tag.get("kind") == "source_keyword"
    ]

    wiki_index = build_wiki_index(links)

    print("=== 1단계: Wikipedia QID 일괄 조회 ===")
    print("Wikipedia 고유 QID:", len(wiki_index))

    wiki_entities = fetch_entities(wiki_index.keys())

    results = []
    unmatched = []

    for tag in keywords:
        identified = identify_from_wikipedia(
            tag, wiki_entities, wiki_index
        )

        if identified:
            results.append({
                "keyword": tag["name"],
                "kind": tag["kind"],
                "source_evidence": tag.get("evidence", []),
                **identified,
            })

        else:
            unmatched.append(tag)

    print("Wikipedia 단계 처리:", len(results))
    print("추가 검색 대상:", len(unmatched))

    print()
    print("=== 2단계: 미매칭 키워드 검색 ===")

    search_results = {}

    for index, tag in enumerate(unmatched, start=1):
        keyword = tag["name"]

        print(f"[{index}/{len(unmatched)}] {keyword}")

        try:
            search_results[keyword] = search_keyword(keyword)

        except HTTPError as error:
            if error.code == 429:
                print("요청 제한 지속: 추가 검색 중단")
                break
            raise

        time.sleep(2)

    # 검색 결과에 등장한 QID를 한 번에 조회
    all_qids = {
        qid
        for qids in search_results.values()
        for qid in qids
    }

    print()
    print("=== 3단계: 검색 후보 일괄 조회 ===")
    print("고유 후보 QID:", len(all_qids))

    try:
        searched_entities = fetch_entities(all_qids)
    except HTTPError as error:
        if error.code == 429:
            print("요청 제한: 상세정보 조회 보류")
            searched_entities = {}
        else:
            raise

    for tag in unmatched:
        keyword = tag["name"]
        qids = search_results.get(keyword)

        if qids is None:
            status = "not_searched"
            reason = "api_search_not_completed"
            candidates = []

        elif qids and not searched_entities:
            status = "not_searched"
            reason = "entity_details_not_available"
            candidates = []

        else:
            candidates = matching_candidates(
                keyword,
                {
                    qid: searched_entities[qid]
                    for qid in qids
                    if qid in searched_entities
                },
            )

            if candidates:
                # 위키 링크 외부에서 찾은 QID는 자동 확정하지 않는다.
                status = "review"
                reason = "exact_name_candidates_from_search"
            else:
                status = "unresolved"
                reason = "no_exact_name_candidate"

        results.append({
            "keyword": keyword,
            "kind": tag["kind"],
            "source_evidence": tag.get("evidence", []),
            "status": status,
            "matched_qid": None,
            "reason": reason,
            "candidates": candidates,
            "wikipedia_evidence": [],
        })

    # DB 키워드의 원래 순서 복원
    order = {
        tag["name"]: index
        for index, tag in enumerate(keywords)
    }

        # 모든 후보에서 P31 유형 QID 수집
    p31_qids = set()

    for row in results:
        for candidate in row.get("candidates", []):
            p31_qids.update(
                candidate.get("instance_of_qids", [])
            )

    # P31 유형명 일괄 조회
    print("=== 4단계: P31 유형명 조회 ===")
    print("고유 P31 유형 QID:", len(p31_qids))

    p31_labels = fetch_p31_labels(p31_qids)

    # 각 후보에 P31 유형명 추가
    for row in results:
        for candidate in row.get("candidates", []):
            candidate["instance_of_types"] = [
                p31_labels.get(
                    qid,
                    {"qid": qid, "ko": None, "en": None},
                )
                for qid in candidate.get("instance_of_qids", [])
            ]

    results.sort(key=lambda r: order[r["keyword"]])

    matched_qids = {
        row["matched_qid"]
        for row in results
        if row["matched_qid"]
    }

    # 링크 중복을 제거해 QID별 신규 후보로 정리
    wikipedia_unmatched = [
        {
            "qid": qid,
            "links": evidence,
            "status": "candidate",
        }
        for qid, evidence in wiki_index.items()
        if qid not in matched_qids
    ]

    summary = {
        "total_db_keywords": len(keywords),
        "matched": sum(
            r["status"] == "matched" for r in results
        ),
        "review": sum(
            r["status"] == "review" for r in results
        ),
        "unresolved": sum(
            r["status"] == "unresolved" for r in results
        ),
        "not_searched": sum(
            r["status"] == "not_searched" for r in results
        ),
        "wikipedia_unmatched_unique_qids": len(
            wikipedia_unmatched
        ),
    }

    output = {
        "content_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy": "db_first_wikipedia_qid_matching_v4_p31_labels",
        "summary": summary,
        "db_keywords": results,
        "other_source_tags": [
            tag for tag in source_tags
            if tag.get("kind") != "source_keyword"
        ],
        "wikipedia_unmatched": wikipedia_unmatched,
        "note": (
            "matched는 기존 Wikipedia 링크와 정확한 명칭이 "
            "일치한다는 의미이며, 콘텐츠 관련성 검증은 별도다. "
            "위키 미매칭 링크도 신규 토픽 확정이 아니다."
        ),
    }

    output_path = (
        OUTPUT_DIR / f"keyword_qid_matches_{cid}_v4.json"
    )

    save_new_json(output_path, output)

    print()
    print("=== 최종 결과 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("저장:", output_path)


if __name__ == "__main__":
    main()