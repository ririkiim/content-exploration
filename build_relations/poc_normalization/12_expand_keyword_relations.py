import argparse
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE = Path("data/enrichment")
NORMALIZATION_DIR = BASE / "normalization"
OUTPUT_DIR = BASE / "relations"

WIKIPEDIA_API = "https://ko.wikipedia.org/w/api.php"

USER_AGENT = (
    "ContentExplorationCapstone/0.1 "
    "(academic research; Python urllib)"
)

# 관계 표현은 일반적인 한국어 문장 패턴만 사용한다.
# 특정 작품이나 인물에 대한 규칙은 넣지 않는다.
RELATION_PATTERNS = {
    "caused_by": [
        r"원인(?:이|으로|은|가|이었|가 되었)",
        r"계기(?:가|로|를)",
        r"촉발",
        r"발단",
    ],
    "influenced_by": [
        r"영향을 (?:받|미치)",
        r"영향을 주",
        r"영향을 끼치",
    ],
    "participated_in": [
        r"참여",
        r"참가",
        r"가담",
        r"주도",
    ],
    "associated_with": [
        r"관련(?:이|된|하여|해)",
        r"연관(?:이|된|되어)",
        r"연결(?:된|되어)",
    ],
    "part_of": [
        r"일환",
        r"일부",
        r"소속",
        r"포함",
    ],
    "occurred_during": [
        r"당시",
        r"기간에",
        r"시기에",
        r"무렵",
    ],
    "commemorates": [
        r"기념",
        r"추모",
    ],
}

# 관계 표현이 발견되더라도 의미적 방향이 불명확할 수 있다.
# 따라서 아래 유형은 모두 후보 단계에서만 사용한다.
RELATION_STATUS = "proposed"


def load_json(path, required=True):
    if not path.exists():
        if required:
            raise FileNotFoundError(
                f"필수 입력 파일이 없습니다: {path}"
            )
        return None

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def make_id(prefix, *parts):
    raw = "|".join(str(part) for part in parts)

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:16]

    return f"{prefix}:{digest}"


def normalize_text(value):
    value = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )

    value = value.casefold()
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def compact_text(value):
    value = normalize_text(value)

    return re.sub(
        r"[\W_]+",
        "",
        value,
        flags=re.UNICODE,
    )


def unique_strings(values):
    result = []
    seen = set()

    for value in values:
        if not isinstance(value, str):
            continue

        value = value.strip()

        if not value:
            continue

        key = normalize_text(value)

        if key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def unique_records(records):
    result = []
    seen = set()

    for record in records:
        key = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(record)

    return result


def wikipedia_request(params, timeout=20):
    query = {
        "format": "json",
        "formatversion": "2",
        **params,
    }

    url = WIKIPEDIA_API + "?" + urlencode(query)

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urlopen(
        request,
        timeout=timeout,
    ) as response:
        return json.load(response)


def wikipedia_search(keyword, limit=5):
    """
    QID 없이 키워드 이름으로 Wikipedia 검색.

    검색 결과는 문서 후보일 뿐,
    토픽과 문서가 같은 개념이라는 확정 근거는 아니다.
    """
    data = wikipedia_request({
        "action": "query",
        "list": "search",
        "srsearch": keyword,
        "srnamespace": 0,
        "srlimit": limit,
    })

    return data.get("query", {}).get(
        "search",
        [],
    )


def select_wikipedia_page(keyword, results):
    """
    검색 결과에서 표기가 일치하는 문서를 우선 선택.

    정확히 일치하는 제목이 없으면 자동 선택하지 않는다.
    잘못된 Wikipedia 문서로 확장되는 것을 방지한다.
    """
    keyword_normalized = normalize_text(keyword)
    keyword_compact = compact_text(keyword)

    exact = []

    for item in results:
        title = item.get("title", "")

        if normalize_text(title) == keyword_normalized:
            exact.append(item)
            continue

        # 공백·구두점 차이만 허용
        if (
            len(keyword_compact) >= 5
            and compact_text(title) == keyword_compact
        ):
            exact.append(item)

    if len(exact) == 1:
        return {
            "status": "matched_title",
            "page": exact[0],
        }

    if len(exact) > 1:
        return {
            "status": "ambiguous_title",
            "page": None,
        }

    return {
        "status": "no_exact_title",
        "page": None,
    }


def fetch_wikipedia_page(page_id):
    """
    Wikipedia 본문과 문서 메타데이터 조회.
    """
    data = wikipedia_request({
        "action": "query",
        "pageids": str(page_id),
        "prop": "extracts|info|revisions",
        "explaintext": 1,
        "inprop": "url",
        "rvprop": "ids",
        "redirects": 1,
    })

    pages = data.get("query", {}).get(
        "pages",
        [],
    )

    if not pages:
        return None

    page = pages[0]

    if page.get("missing"):
        return None

    revisions = page.get("revisions", [])

    revision_id = (
        revisions[0].get("revid")
        if revisions
        else None
    )

    return {
        "page_id": page.get("pageid"),
        "title": page.get("title"),
        "url": page.get("fullurl"),
        "revision_id": revision_id,
        "text": page.get("extract", ""),
    }


def fetch_wikipedia_links(page_id, max_links=150):
    """
    문서 내부 링크를 탐색 후보로만 수집한다.
    링크 자체를 관계 근거로 사용하지 않는다.
    """
    links = []
    continuation = {}

    while len(links) < max_links:
        remaining = max_links - len(links)

        params = {
            "action": "query",
            "pageids": str(page_id),
            "prop": "links",
            "plnamespace": 0,
            "pllimit": min(remaining, 500),
            **continuation,
        }

        data = wikipedia_request(params)

        pages = data.get("query", {}).get(
            "pages",
            [],
        )

        if not pages:
            break

        for item in pages[0].get("links", []):
            title = item.get("title")

            if title:
                links.append(title)

        continuation = data.get("continue")

        if not continuation:
            break

    return unique_strings(links)[:max_links]


def split_sentences(text):
    """
    Wikipedia 평문을 문장 단위로 분리한다.

    완벽한 한국어 문장 분리기는 아니므로
    원문을 함께 보존하고 자동 검증하지 않는다.
    """
    text = re.sub(r"\r\n?", "\n", text or "")

    paragraphs = re.split(
        r"\n+",
        text,
    )

    sentences = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        chunks = re.split(
            r"(?<=[.!?。])\s+",
            paragraph,
        )

        for chunk in chunks:
            chunk = chunk.strip()

            if not chunk:
                continue

            sentences.append(chunk)

    return sentences


def detect_relation_cues(sentence):
    """
    문장에 관계 표현이 존재하는지 검사.

    주의:
    표현이 존재한다고 실제 두 개체 사이의
    관계가 입증되는 것은 아니다.
    """
    matches = []

    for relation_type, patterns in (
        RELATION_PATTERNS.items()
    ):
        for pattern in patterns:
            found = re.search(
                pattern,
                sentence,
            )

            if not found:
                continue

            matches.append({
                "relation_type": relation_type,
                "matched_text": found.group(0),
                "pattern": pattern,
            })

            break

    return matches


def find_target_mentions(
    sentence,
    links,
    source_keyword,
):
    """
    문장에 등장하는 Wikipedia 링크 제목을 찾는다.

    문서 링크 전체를 관계로 만드는 것이 아니라
    실제 본문에 언급된 제목만 후보로 삼는다.
    """
    sentence_normalized = normalize_text(sentence)
    source_normalized = normalize_text(
        source_keyword
    )

    mentions = []

    for title in links:
        title_normalized = normalize_text(title)

        if len(title_normalized) < 2:
            continue

        if title_normalized == source_normalized:
            continue

        if title_normalized not in sentence_normalized:
            continue

        mentions.append(title)

    return unique_strings(mentions)


def extract_relation_candidates(
    content_id,
    topic,
    page,
    links,
    max_sentences=150,
    max_targets_per_sentence=5,
):
    """
    Wikipedia 본문에서 관계 표현과
    관련 개체가 함께 등장하는 문장을 수집한다.

    관계 표현의 방향과 실제 의미는
    아직 검증되지 않은 후보 상태로 저장한다.
    """
    topic_id = topic["topic_id"]
    keyword = topic["topic_name"]

    sentences = split_sentences(
        page["text"]
    )

    relations = []

    for sentence_index, sentence in enumerate(
        sentences[:max_sentences]
    ):
        cues = detect_relation_cues(sentence)

        if not cues:
            continue

        targets = find_target_mentions(
            sentence,
            links,
            keyword,
        )

        if not targets:
            continue

        for target in targets[
            :max_targets_per_sentence
        ]:
            for cue in cues:
                relation_id = make_id(
                    "wiki_relation",
                    content_id,
                    topic_id,
                    page["page_id"],
                    sentence_index,
                    target,
                    cue["relation_type"],
                )

                relations.append({
                    "relation_id": relation_id,

                    "source_topic_id": topic_id,
                    "source_topic_name": keyword,

                    "source_qid": topic.get("qid"),
                    "source_qid_candidates": (
                        topic.get(
                            "qid_candidates",
                            [],
                        )
                    ),

                    "target_topic_id": None,
                    "target_topic_name": target,
                    "target_qid": None,

                    # 문장 내 표현을 기반으로 한
                    # 관계 유형 후보일 뿐이다.
                    "relation_type_candidate": (
                        cue["relation_type"]
                    ),

                    "relation_direction_confirmed": False,

                    "matched_expression": (
                        cue["matched_text"]
                    ),

                    "evidence_source": (
                        "wikipedia_article_text"
                    ),

                    "evidence_text": sentence,

                    "evidence_url": page["url"],
                    "evidence_page_id": (
                        page["page_id"]
                    ),
                    "evidence_revision_id": (
                        page["revision_id"]
                    ),
                    "evidence_sentence_index": (
                        sentence_index
                    ),

                    "candidate_discovery_method": (
                        "linked_title_mentioned_"
                        "in_relation_cue_sentence"
                    ),

                    "evidence_status": "unchecked",
                    "review_status": RELATION_STATUS,

                    "limitations": [
                        "The target title is mentioned "
                        "in a sentence containing a "
                        "relation expression.",
                        "The expression may refer to "
                        "another entity.",
                        "Relation direction and "
                        "semantics are unverified.",
                    ],
                })

    return unique_records(relations)


def prepare_expansion_topics(data):
    """
    모든 토픽을 유지하되
    source_keyword를 가장 먼저 처리한다.
    """
    topics = data.get(
        "expansion_queue",
        [],
    )

    def priority(topic):
        origins = topic.get(
            "candidate_origins",
            [],
        )

        if "source_keyword" in origins:
            return 0

        if "wikipedia_explicit_theme" in origins:
            return 1

        if "wikidata_subject" in origins:
            return 2

        return 3

    return sorted(
        topics,
        key=lambda item: (
            priority(item),
            item.get("topic_name", ""),
        ),
    )


def build_wikidata_relations(data):
    """
    11 v2의 관계를 그대로 보존한다.

    Wikidata 관계는 새로 검증하거나
    확정된 QID로 승격하지 않는다.
    """
    return data.get(
        "topic_relations",
        [],
    )


def build_existing_evidence_index(bundle):
    """
    08 Evidence Bundle의 기존 근거 문서를
    참고용으로 보존한다.
    """
    if not bundle:
        return []

    return bundle.get(
        "evidence_documents",
        [],
    )


def process_topic(
    content_id,
    topic,
    search_limit,
    max_links,
    max_sentences,
    request_delay,
):
    keyword = topic["topic_name"]

    result = {
        "topic_id": topic["topic_id"],
        "topic_name": keyword,

        "candidate_origins": topic.get(
            "candidate_origins",
            [],
        ),

        "qid": topic.get("qid"),
        "qid_candidates": topic.get(
            "qid_candidates",
            [],
        ),

        "search_status": None,
        "search_results": [],

        "selected_wikipedia_page": None,

        "relations_generated": 0,
        "discovery_candidates_generated": 0,

        "error": None,
    }

    relations = []
    discovery = []

    try:
        search_results = wikipedia_search(
            keyword,
            limit=search_limit,
        )

        result["search_results"] = [
            {
                "title": item.get("title"),
                "pageid": item.get("pageid"),
            }
            for item in search_results
        ]

        selection = select_wikipedia_page(
            keyword,
            search_results,
        )

        result["search_status"] = selection[
            "status"
        ]

        selected = selection["page"]

        if selected is None:
            return result, relations, discovery

        time.sleep(request_delay)

        page = fetch_wikipedia_page(
            selected["pageid"]
        )

        if not page:
            result["search_status"] = (
                "page_fetch_failed"
            )
            return result, relations, discovery

        result["selected_wikipedia_page"] = {
            "page_id": page["page_id"],
            "title": page["title"],
            "url": page["url"],
            "revision_id": page["revision_id"],
        }

        time.sleep(request_delay)

        links = fetch_wikipedia_links(
            page["page_id"],
            max_links=max_links,
        )

        for title in links:
            discovery.append({
                "candidate_id": make_id(
                    "wiki_discovery",
                    content_id,
                    topic["topic_id"],
                    page["page_id"],
                    title,
                ),

                "source_topic_id": topic["topic_id"],
                "source_topic_name": keyword,

                "candidate_name": title,

                "source_page_title": page["title"],
                "source_page_url": page["url"],
                "source_page_id": page["page_id"],
                "source_revision_id": (
                    page["revision_id"]
                ),

                "discovery_source": (
                    "wikipedia_article_link"
                ),

                "relation_claimed": False,
                "verification_role": (
                    "candidate_discovery_only"
                ),
            })

        relations = extract_relation_candidates(
            content_id,
            topic,
            page,
            links,
            max_sentences=max_sentences,
        )

        result["relations_generated"] = len(
            relations
        )

        result[
            "discovery_candidates_generated"
        ] = len(discovery)

        result["search_status"] = (
            "page_processed"
        )

    except Exception as exc:
        result["search_status"] = "error"
        result["error"] = (
            f"{type(exc).__name__}: {exc}"
        )

    return result, relations, discovery


def process(
    content_id,
    search_limit=5,
    max_links=150,
    max_sentences=150,
    request_delay=0.3,
):
    input_path = (
        NORMALIZATION_DIR
        / f"topic_relations_{content_id}_v2.json"
    )

    bundle_path = (
        NORMALIZATION_DIR
        / f"evidence_bundle_{content_id}_v3.json"
    )

    output_path = (
        OUTPUT_DIR
        / f"topic_relation_candidates_{content_id}_v1.json"
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # 기존 결과 덮어쓰기 방지
    if output_path.exists():
        raise FileExistsError(
            f"이미 결과가 존재합니다: {output_path}"
        )

    data = load_json(input_path)

    bundle = load_json(
        bundle_path,
        required=False,
    )

    topics = prepare_expansion_topics(data)

    wikipedia_relations = []
    wikipedia_discovery = []
    topic_results = []

    print(
        f"\n[{content_id}] "
        f"관계 확장 시작: {len(topics)}개 토픽"
    )

    for index, topic in enumerate(
        topics,
        start=1,
    ):
        keyword = topic["topic_name"]

        print(
            f"[{index}/{len(topics)}] "
            f"{keyword}"
        )

        result, relations, discovery = (
            process_topic(
                content_id=content_id,
                topic=topic,
                search_limit=search_limit,
                max_links=max_links,
                max_sentences=max_sentences,
                request_delay=request_delay,
            )
        )

        topic_results.append(result)

        wikipedia_relations.extend(relations)
        wikipedia_discovery.extend(discovery)

        print(
            f"  검색: {result['search_status']} "
            f"| 관계 후보: {len(relations)} "
            f"| 링크 후보: {len(discovery)}"
        )

        time.sleep(request_delay)

    wikipedia_relations = unique_records(
        wikipedia_relations
    )

    wikipedia_discovery = unique_records(
        wikipedia_discovery
    )

    wikidata_relations = (
        build_wikidata_relations(data)
    )

    existing_evidence = (
        build_existing_evidence_index(bundle)
    )

    status_counts = Counter(
        result["search_status"]
        for result in topic_results
    )

    origin_counts = Counter()

    for topic in topics:
        for origin in topic.get(
            "candidate_origins",
            [],
        ):
            origin_counts[origin] += 1

    output = {
        "schema_version": (
            "topic_relation_candidates_v1"
        ),

        "content_id": content_id,

        "source_files": {
            "topic_relations_v2": input_path.name,
            "evidence_bundle_v3": (
                bundle_path.name
                if bundle
                else None
            ),
        },

        # 기존 콘텐츠-토픽 관계 유지
        "content_topic_relations": data.get(
            "content_topic_relations",
            [],
        ),

        # 기존 Wikidata 관계 유지
        "wikidata_relation_candidates": (
            wikidata_relations
        ),

        # 이번에 새로 발견한 후보
        "wikipedia_relation_candidates": (
            wikipedia_relations
        ),

        # 링크 자체는 관계가 아니다.
        "wikipedia_discovery_candidates": (
            wikipedia_discovery
        ),

        # 기존 11 v2의 탐색 후보도 유지
        "previous_discovery_candidates": (
            data.get(
                "discovery_candidates",
                [],
            )
        ),

        "existing_evidence_documents": (
            existing_evidence
        ),

        "topic_processing_results": (
            topic_results
        ),

        "summary": {
            "total_topics": len(topics),

            "source_keyword_topics": (
                origin_counts.get(
                    "source_keyword",
                    0,
                )
            ),

            "content_topic_relations": len(
                data.get(
                    "content_topic_relations",
                    [],
                )
            ),

            "existing_wikidata_relations": len(
                wikidata_relations
            ),

            "new_wikipedia_relation_candidates": (
                len(wikipedia_relations)
            ),

            "new_wikipedia_discovery_candidates": (
                len(wikipedia_discovery)
            ),

            "search_status_counts": dict(
                status_counts
            ),

            "verified_relations": 0,
        },

        "policy": {
            "primary_entity": "keyword_or_topic",
            "source_keywords_first": True,

            "qid_required": False,
            "qid_auto_confirmed": False,

            "wikipedia_links_as_relation_evidence": (
                False
            ),

            "relation_auto_verified": False,

            "llm_used": False,
            "database_written": False,

            "existing_files_overwritten": False,
        },

        "limitations": [
            "Only Korean Wikipedia is queried.",
            "Pages are selected automatically only "
            "when their titles match the keyword.",
            "No exact title match does not mean "
            "that a relevant article does not exist.",
            "Wikipedia relation candidates are "
            "based on sentence-level lexical cues.",
            "Co-occurrence and lexical cues do not "
            "prove a semantic relation.",
            "Relation direction is not verified.",
            "Existing Wikidata relations are "
            "preserved without revalidation.",
            "Candidate content discovery and "
            "verification are not implemented.",
        ],
    }

    # x 모드: 기존 파일이 있으면 실패
    with output_path.open(
        "x",
        encoding="utf-8",
    ) as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    summary = output["summary"]

    print("\n" + "=" * 55)
    print(f"[{content_id}] 완료")
    print("=" * 55)

    print(
        "전체 토픽:",
        summary["total_topics"],
    )

    print(
        "원본 키워드 토픽:",
        summary["source_keyword_topics"],
    )

    print(
        "콘텐츠-토픽 관계:",
        summary["content_topic_relations"],
    )

    print(
        "기존 Wikidata 관계:",
        summary["existing_wikidata_relations"],
    )

    print(
        "새 Wikipedia 관계 후보:",
        summary[
            "new_wikipedia_relation_candidates"
        ],
    )

    print(
        "새 Wikipedia 링크 후보:",
        summary[
            "new_wikipedia_discovery_candidates"
        ],
    )

    print(
        "Wikipedia 처리 상태:",
        summary["search_status_counts"],
    )

    print(f"\n저장: {output_path}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--content-id",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--search-limit",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--max-links",
        type=int,
        default=150,
    )

    parser.add_argument(
        "--max-sentences",
        type=int,
        default=150,
    )

    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.3,
    )

    args = parser.parse_args()

    process(
        content_id=args.content_id,
        search_limit=args.search_limit,
        max_links=args.max_links,
        max_sentences=args.max_sentences,
        request_delay=args.request_delay,
    )


if __name__ == "__main__":
    main()