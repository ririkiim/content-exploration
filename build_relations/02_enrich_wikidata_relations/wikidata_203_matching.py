"""TMDb/ISBN/제목·저자 기반 콘텐츠 QID 매칭."""
import hashlib
import json
import re
import time
from collections import defaultdict
from urllib.error import HTTPError, URLError

from wikidata_201_config import CACHE_DIR, REQUEST_DELAY_SECONDS, SPARQL_BATCH_SIZE, WIKIDATA_API
from wikidata_202_client import chunks, request_json, request_sparql

def lookup_property_qids(values, property_id):
    """ISBN 속성으로 QID를 조회한다.

    반환값은 (result, failed_values)다. result의 빈 목록은 조회 성공 후 미매칭을
    뜻하고, failed_values는 서버 오류 등으로 결과를 확인하지 못한 원본 ISBN이다.
    성공 결과(미매칭 포함)만 캐시하므로 실패한 ISBN은 다음 실행에서 다시 조회된다.
    """
    original_to_normalized = {}
    for value in values:
        if value is None or not str(value).strip():
            continue
        original = str(value).strip()
        normalized = re.sub(r"[^0-9Xx]", "", original).upper()
        if normalized:
            original_to_normalized[original] = normalized

    result = {original: [] for original in original_to_normalized}
    if not original_to_normalized:
        return result, set()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"property_lookup_{property_id}.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, json.JSONDecodeError):
        cache = {}

    normalized_to_originals = defaultdict(list)
    for original, normalized in original_to_normalized.items():
        normalized_to_originals[normalized].append(original)

    pending = [
        (original, normalized)
        for normalized, originals in normalized_to_originals.items()
        if normalized not in cache
        for original in [originals[0]]
    ]
    qids_by_normalized = {
        normalized: sorted(set(cache.get(normalized, {}).get("qids", [])))
        for normalized in normalized_to_originals
        if normalized in cache
    }
    failed_normalized = set()

    def query_batch(batch):
        if not batch:
            return {}, set()
        values_clause = " ".join(
            f"({json.dumps(original, ensure_ascii=False)} {json.dumps(normalized, ensure_ascii=False)})"
            for original, normalized in batch
        )
        query = f"""
        SELECT DISTINCT ?lookupValue ?normalizedValue ?item WHERE {{
          VALUES (?lookupValue ?normalizedValue) {{ {values_clause} }}
          ?item wdt:{property_id} ?isbn .
          BIND(UCASE(REPLACE(STR(?isbn), "[^0-9Xx]", "")) AS ?normalizedISBN)
          FILTER(?normalizedISBN = ?normalizedValue)
        }}
        """
        try:
            data = request_sparql(query)
        except HTTPError as error:
            if error.code == 429:
                raise
            if error.code not in (500, 502, 503, 504):
                return {}, {normalized for _, normalized in batch}
            if len(batch) == 1:
                print(f"[ISBN 조회 실패] {property_id} / {batch[0][0]} / HTTP {error.code}")
                return {}, {batch[0][1]}
            middle = len(batch) // 2
            print(f"[배치 분할 재시도] {property_id}: {len(batch)}개 → {len(batch[:middle])}개 + {len(batch[middle:])}개")
            time.sleep(3)
            left_map, left_failed = query_batch(batch[:middle])
            right_map, right_failed = query_batch(batch[middle:])
            merged = defaultdict(set)
            for key, qids in list(left_map.items()) + list(right_map.items()):
                merged[key].update(qids)
            return {key: sorted(qids) for key, qids in merged.items()}, left_failed | right_failed
        except (URLError, TimeoutError, ConnectionError, OSError, RuntimeError) as error:
            if len(batch) > 1:
                middle = len(batch) // 2
                print(f"[배치 분할 재시도] {property_id}: {len(batch)}개 → {len(batch[:middle])}개 + {len(batch[middle:])}개 ({type(error).__name__})")
                time.sleep(3)
                left_map, left_failed = query_batch(batch[:middle])
                right_map, right_failed = query_batch(batch[middle:])
                merged = defaultdict(set)
                for key, qids in list(left_map.items()) + list(right_map.items()):
                    merged[key].update(qids)
                return {key: sorted(qids) for key, qids in merged.items()}, left_failed | right_failed
            print(f"[ISBN 조회 실패] {property_id} / {batch[0][0]} / {type(error).__name__}: {error}")
            return {}, {batch[0][1]}

        found = defaultdict(set)
        for binding in data.get("results", {}).get("bindings", []):
            normalized = binding.get("normalizedValue", {}).get("value", "")
            qid = binding.get("item", {}).get("value", "").rsplit("/", 1)[-1]
            if normalized and qid.startswith("Q"):
                found[normalized].add(qid)
        # A successful query with no result is still a confirmed empty result.
        return {key: sorted(qids) for key, qids in found.items()}, set()

    for batch_start in range(0, len(pending), SPARQL_BATCH_SIZE):
        batch = pending[batch_start:batch_start + SPARQL_BATCH_SIZE]
        try:
            found, failed = query_batch(batch)
        except HTTPError as error:
            if error.code != 429:
                raise
            print(f"[요청 제한] {property_id} 조회에서 429가 발생했습니다. 남은 ISBN은 미조회 상태로 둡니다.")
            failed_normalized.update(normalized for _, normalized in pending[batch_start:])
            break
        for _, normalized in batch:
            if normalized not in failed:
                qids_by_normalized[normalized] = found.get(normalized, [])
                cache[normalized] = {"qids": qids_by_normalized[normalized]}
        failed_normalized.update(failed)
        time.sleep(REQUEST_DELAY_SECONDS)

    # Persist only confirmed results. Failed values intentionally remain uncached.
    try:
        cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        print(f"[ISBN 캐시 저장 실패] {cache_file}: {error}")

    failed_values = set()
    for original, normalized in original_to_normalized.items():
        if normalized in failed_normalized:
            failed_values.add(original)
            continue
        result[original] = qids_by_normalized.get(normalized, [])
    return result, failed_values


def lookup_tmdb_qids(values, property_id):

          """

          TMDb ID를 Wikidata 속성값과 정확히 비교해 QID를 조회한다.

          영화 P4947, TV P4983에 사용한다.

          """

          result = {

                    str(value): []

                    for value in values

                    if value and str(value).strip()

          }


          unique_values = list(result.keys())


          for value_batch in chunks(unique_values, SPARQL_BATCH_SIZE):

                    values_clause = " ".join(

                              json.dumps(value, ensure_ascii=False)

                              for value in value_batch

                    )


                    query = f"""

                    SELECT DISTINCT ?lookupValue ?item WHERE {{

                              VALUES ?lookupValue {{

                                        {values_clause}

                              }}


                              ?item wdt:{property_id} ?lookupValue .

                    }}

                    """


                    data = request_sparql(query)


                    for binding in data.get("results", {}).get("bindings", []):

                              lookup_value = binding["lookupValue"]["value"]

                              item_uri = binding["item"]["value"]

                              qid = item_uri.rsplit("/", 1)[-1]


                              if qid.startswith("Q"):

                                        result.setdefault(lookup_value, []).append(qid)


                    time.sleep(REQUEST_DELAY_SECONDS)


          for value in result:

                    result[value] = sorted(set(result[value]))


          return result


def classify_lookup_result(qids):

          """매칭 결과를 성공·미매칭·모호로 구분한다."""

          qids = sorted(set(qids or []))


          if len(qids) == 1:

                    return qids[0], "matched"


          if not qids:

                    return None, "unmatched"


          return None, "ambiguous"


def search_wikidata_title_candidates(title):
    """한국어·영어 제목 검색 결과를 캐시하고 QID 후보를 합친다."""
    if not title or not str(title).strip():
        return []

    title = str(title).strip()
    qids = set()
    search_cache_dir = CACHE_DIR / "title_search"
    search_cache_dir.mkdir(parents=True, exist_ok=True)

    for language in ("ko", "en"):
        cache_key = hashlib.sha256(f"{language}\0{title.casefold()}".encode("utf-8")).hexdigest()
        cache_file = search_cache_dir / f"{cache_key}.json"
        cached = None
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None

        if isinstance(cached, list):
            language_qids = cached
        else:
            data = request_json(
                WIKIDATA_API,
                {
                    "action": "wbsearchentities",
                    "search": title,
                    "language": language,
                    "uselang": language,
                    "type": "item",
                    "limit": 50,
                    "format": "json",
                },
            )
            language_qids = sorted({
                item.get("id", "")
                for item in data.get("search", [])
                if item.get("id", "").startswith("Q")
            })
            # Cache only successful API responses, including a successful empty result.
            cache_file.write_text(json.dumps(language_qids, ensure_ascii=False), encoding="utf-8")
            time.sleep(REQUEST_DELAY_SECONDS)

        qids.update(language_qids)

    return sorted(qids)



def match_book_by_title_author(title, author):
    """
    제목 검색 후보 중 제목과 저자가 일치하는 Wikidata 항목을 찾는다.

    보완 사항:
    1. 제목 끝에 권 번호가 있으면 권 번호를 제거한 제목도 검색한다.
    2. '김만중'과 '서포 김만중'을 동일 저자로 비교한다.
    3. 검색 후보 및 매칭 근거를 출력한다.

    반환:
        (qid, "matched")
        (None, "unmatched")
        (None, "ambiguous")
        (None, "lookup_failed")
    """
    title = str(title or "").strip()
    author = normalize_author(author)

    if not title or not author:
        return None, "unmatched"

    def normalize_text(value):
        return " ".join(str(value or "").split()).casefold()

    def canonical_author_name(value):
        """
        확인된 저자 이명만 명시적으로 통일한다.
        무관한 저자끼리 잘못 매칭되는 것을 방지하기 위해
        모든 이름에서 임의로 첫 단어를 제거하지 않는다.
        """
        normalized = normalize_text(normalize_author(value))

        author_aliases = {
            "김만중": "김만중",
            "서포 김만중": "김만중",
        }

        return author_aliases.get(normalized, normalized)

    expected_author = canonical_author_name(author)

    # 원본 제목은 유지하고, 끝에 붙은 숫자 권 번호만 제거한
    # 검색용 제목을 추가한다. 예: '토지 1' -> '토지'
    title_variants = [title]

    volume_match = re.match(r"^(.+?)\s+\d+$", title)
    if volume_match:
        base_title = volume_match.group(1).strip()

        if base_title and base_title not in title_variants:
            title_variants.append(base_title)

    print(
        f"\n[도서 후보 분석] 제목={title!r}, "
        f"저자={author!r}, "
        f"검색 제목={title_variants}"
    )

    # 각 검색 제목의 후보 QID를 모은다.
    candidate_qids = set()
    search_failed = False

    for search_title in title_variants:
        try:
            found_qids = search_wikidata_title_candidates(search_title)
            candidate_qids.update(found_qids or [])

            print(
                f"  [제목 검색] {search_title!r}: "
                f"후보 {len(found_qids or [])}개"
            )

        except (
            HTTPError,
            URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            RuntimeError,
        ) as error:
            search_failed = True

            print(
                f"[도서 제목 후보 검색 실패] {search_title}: "
                f"{type(error).__name__}: {error}"
            )

    if not candidate_qids:
        if search_failed:
            return None, "lookup_failed"

        print("[도서 후보 분석] 검색 후보 없음")
        return None, "unmatched"

    values_clause = " ".join(
        f"wd:{qid}" for qid in sorted(candidate_qids)
    )

    # 저자 정보가 없는 후보도 진단할 수 있도록 OPTIONAL 사용
    query = f"""
    SELECT DISTINCT ?item ?titleName ?authorName WHERE {{
        VALUES ?item {{ {values_clause} }}

        {{
            ?item rdfs:label ?titleName .
        }}
        UNION
        {{
            ?item skos:altLabel ?titleName .
        }}

        FILTER(LANG(?titleName) IN ("ko", "en"))

        OPTIONAL {{
            ?item wdt:P50 ?author .

            {{
                ?author rdfs:label ?authorName .
            }}
            UNION
            {{
                ?author skos:altLabel ?authorName .
            }}

            FILTER(LANG(?authorName) IN ("ko", "en"))
        }}
    }}
    """

    try:
        data = request_sparql(query)

    except (
        HTTPError,
        URLError,
        TimeoutError,
        ConnectionError,
        OSError,
        RuntimeError,
    ) as error:
        print(
            f"[도서 제목·작가 조회 실패] {title} / {author}: "
            f"{type(error).__name__}: {error}"
        )
        return None, "lookup_failed"

    expected_titles = {
        normalize_text(value) for value in title_variants
    }

    # 같은 QID에 여러 제목·저자 레이블이 존재할 수 있으므로
    # QID별로 정보를 합친다.
    candidate_details = {}

    for binding in data.get("results", {}).get("bindings", []):
        candidate_title = binding["titleName"]["value"]
        candidate_author = binding.get("authorName", {}).get("value", "")
        item_uri = binding["item"]["value"]
        qid = item_uri.rsplit("/", 1)[-1]

        detail = candidate_details.setdefault(
            qid,
            {
                "titles": set(),
                "authors": set(),
                "title_match": False,
                "author_match": False,
            },
        )

        detail["titles"].add(candidate_title)

        title_match = normalize_text(candidate_title) in expected_titles
        detail["title_match"] |= title_match

        if candidate_author:
            detail["authors"].add(candidate_author)

            candidate_author_normalized = canonical_author_name(
                candidate_author
            )

            if candidate_author_normalized == expected_author:
                detail["author_match"] = True

    matched_qids = set()

    print(f"  전체 후보 QID: {len(candidate_qids)}개")

    for qid in sorted(candidate_qids):
        detail = candidate_details.get(qid)

        if detail is None:
            print(
                f"  [후보] QID={qid}, "
                "한국어·영어 제목 레이블을 확인하지 못함"
            )
            continue

        print(
            f"  [후보] QID={qid}, "
            f"제목={sorted(detail['titles'])}, "
            f"저자={sorted(detail['authors']) or ['저자 정보 없음']}, "
            f"제목 일치={detail['title_match']}, "
            f"저자 일치={detail['author_match']}"
        )

        if detail["title_match"] and detail["author_match"]:
            matched_qids.add(qid)

    print(f"  최종 일치 QID: {sorted(matched_qids)}")

    if len(matched_qids) == 1:
        return next(iter(matched_qids)), "matched"

    if len(matched_qids) > 1:
        return None, "ambiguous"

    return None, "unmatched"



def normalize_isbn(value):

          if value is None:

                    return None


          normalized = re.sub(r"[^0-9Xx]", "", str(value)).upper()

          return normalized or None



def normalize_author(author):
    if not author:
        return ""

    author = str(author).strip()

    # 저자·역자·글·그림 표기를 기준으로 뒤쪽을 제거한다.
    # 예: "김만중 저/송성욱 역" -> "김만중"
    author = re.split(
        r"\s+(?:저|역|글|그림)\b|/(?:저|역|글|그림)\b",
        author,
        maxsplit=1,
    )[0]

    return author.strip()



def match_content_qids(contents):
    """콘텐츠별 QID 매칭. 조회 실패는 미매칭과 별도로 유지한다."""
    tmdb_movie_ids, tmdb_tv_ids, isbn13_values, isbn10_values = [], [], [], []
    for content in contents:
        source_ids = content["source_ids"]
        if content["content_type"] == "movie" and source_ids.get("tmdb"):
            tmdb_movie_ids.append(source_ids["tmdb"])
        elif content["content_type"] == "tv" and source_ids.get("tmdb"):
            tmdb_tv_ids.append(source_ids["tmdb"])
        elif content["content_type"] == "book":
            if content.get("isbn13"):
                isbn13_values.append(content["isbn13"])
            if content.get("isbn10"):
                isbn10_values.append(content["isbn10"])

    # TMDb exact-match functions intentionally remain unchanged.
    movie_map = lookup_tmdb_qids(tmdb_movie_ids, "P4947")
    tv_map = lookup_tmdb_qids(tmdb_tv_ids, "P4983")
    isbn13_map, isbn13_failed = lookup_property_qids(isbn13_values, "P212")
    isbn10_map, isbn10_failed = lookup_property_qids(isbn10_values, "P957")

    matched = {}
    stats = defaultdict(int)
    for content in contents:
        content_id = content["content_id"]
        content_type = content["content_type"]
        source_ids = content["source_ids"]
        qid, status = None, "unmatched"

        if content_type == "movie" and source_ids.get("tmdb"):
            qid, status = classify_lookup_result(movie_map.get(source_ids["tmdb"], []))
            stats[f"tmdb_movie_{status}"] += 1
        elif content_type == "tv" and source_ids.get("tmdb"):
            qid, status = classify_lookup_result(tv_map.get(source_ids["tmdb"], []))
            stats[f"tmdb_tv_{status}"] += 1
        elif content_type == "book":
            isbn13, isbn10 = content.get("isbn13"), content.get("isbn10")

            isbn13_lookup_failed = bool(isbn13 and str(isbn13) in isbn13_failed)
            if isbn13 and not isbn13_lookup_failed:
                qid, status = classify_lookup_result(isbn13_map.get(str(isbn13), []))

            # ISBN-13이 정상 조회되어 미매칭일 때 또는 ISBN-13이 없는 경우에만
            # ISBN-10으로 보완한다. ISBN-13 조회 실패 시 ISBN-10의 단일 일치만
            # 확정 근거로 인정하고, 그 외 결과는 lookup_failed로 남긴다.
            if (not isbn13 or (not isbn13_lookup_failed and status == "unmatched")) and isbn10:
                if str(isbn10) in isbn10_failed:
                    status = "lookup_failed"
                else:
                    qid, status = classify_lookup_result(isbn10_map.get(str(isbn10), []))
            elif isbn13_lookup_failed:
                if isbn10 and str(isbn10) not in isbn10_failed:
                    isbn10_qid, isbn10_status = classify_lookup_result(isbn10_map.get(str(isbn10), []))
                    if isbn10_status == "matched":
                        qid, status = isbn10_qid, "matched"
                    else:
                        status = "lookup_failed"
                else:
                    status = "lookup_failed"

            if status == "unmatched":
                try:
                    title_qid, title_status = match_book_by_title_author(
                        content["title"], content.get("author")
                    )
                except Exception as error:
                    # Unexpected lookup errors must not be treated as a genuine no-match.
                    print(f"[도서 매칭 조회 실패] {content.get('title')}: {type(error).__name__}: {error}")
                    title_qid, title_status = None, "lookup_failed"

                if title_status == "matched":
                    qid, status = title_qid, "title_author_matched"
                elif title_status == "ambiguous":
                    status = "title_author_ambiguous"
                elif title_status == "lookup_failed":
                    status = "lookup_failed"
            stats[f"book_{status}"] += 1
        else:
            stats["unsupported_or_missing_source"] += 1
            status = "unmatched"
        
        # 도서별 최종 QID 매칭 결과 출력
        if content_type == "book":
            print(
                f"[도서 매칭 결과] "
                f"content_id={content_id}, "
                f"title={content.get('title')!r}, "
                f"author={content.get('author')!r}, "
                f"isbn13={content.get('isbn13')!r}, "
                f"isbn10={content.get('isbn10')!r}, "
                f"qid={qid!r}, "
                f"status={status}"
            )

        matched[content_id] = {"qid": qid, "status": status}

    return matched, stats
