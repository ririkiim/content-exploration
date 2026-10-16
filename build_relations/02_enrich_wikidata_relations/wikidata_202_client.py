"""Wikidata API/SPARQL 통신 및 엔티티 캐시."""
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from wikidata_201_config import (
    BATCH_SIZE, CACHE_DIR, REQUEST_DELAY_SECONDS, SPARQL_ENDPOINT,
    USER_AGENT, WIKIDATA_API,
)

def chunks(items, size=BATCH_SIZE):

          for index in range(0, len(items), size):

                    yield items[index:index + size]


def request_json(base_url, params):
    """Wikimedia JSON API 호출. 429/일시적 서버 오류만 제한적으로 재시도한다."""
    max_retries = 3

    for attempt in range(max_retries):
        request = Request(
            f"{base_url}?{urlencode(params)}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=45) as response:
                return json.load(response)
        except HTTPError as error:
            retryable = error.code == 429 or error.code in (500, 502, 503, 504)
            if not retryable or attempt >= max_retries - 1:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            if retry_after:
                try:
                    wait_seconds = max(1, min(120, int(retry_after)))
                except (TypeError, ValueError):
                    wait_seconds = 30 if error.code == 429 else 5 * (attempt + 1)
            else:
                wait_seconds = 30 * (attempt + 1) if error.code == 429 else 5 * (attempt + 1)
            print(
                f"Wikimedia API 오류({error.code}). {wait_seconds}초 후 재시도 "
                f"({attempt + 1}/{max_retries})"
            )
            time.sleep(wait_seconds)
        except (TimeoutError, URLError, ConnectionError, OSError) as error:
            if attempt >= max_retries - 1:
                raise
            wait_seconds = 5 * (attempt + 1)
            print(
                f"Wikimedia API 연결 오류({type(error).__name__}). "
                f"{wait_seconds}초 후 재시도 ({attempt + 1}/{max_retries})"
            )
            time.sleep(wait_seconds)

    raise RuntimeError("Wikimedia API 요청이 재시도 한도를 초과했습니다.")


def request_sparql(query):

          max_retries = 3

          timeout_seconds = 90


          for attempt in range(max_retries):

                    request = Request(

                              SPARQL_ENDPOINT,

                              data=urlencode({

                                        "query": query,

                                        "format": "json",

                              }).encode("utf-8"),

                              headers={

                                        "User-Agent": USER_AGENT,

                                        "Accept": "application/sparql-results+json",

                              },

                              method="POST",

                    )


                    try:

                              with urlopen(

                                        request,

                                        timeout=timeout_seconds,

                              ) as response:

                                        return json.load(response)


                    except HTTPError as error:

                              if error.code == 429:

                                        if attempt < max_retries - 1:

                                                  wait_seconds = 30 * (attempt + 1)

                                                  print(

                                                            f"Wikidata 요청 제한(429). "

                                                            f"{wait_seconds}초 후 재시도 "

                                                            f"({attempt + 1}/{max_retries})"

                                                  )

                                                  time.sleep(wait_seconds)

                                                  continue


                                        # 상위 함수가 429를 감지해 남은 배치를 중단할 수 있도록 원래 예외를 보존한다.
                                        raise


                              if error.code in (500, 502, 503, 504):

                                        if attempt < max_retries - 1:

                                                  wait_seconds = 5 * (attempt + 1)

                                                  print(

                                                            f"Wikidata 서버 오류({error.code}). "

                                                            f"{wait_seconds}초 후 재시도 "

                                                            f"({attempt + 1}/{max_retries})"

                                                  )

                                                  time.sleep(wait_seconds)

                                                  continue


                              raise


                    except (TimeoutError, ConnectionError, OSError) as error:

                              if attempt < max_retries - 1:

                                        wait_seconds = 5 * (attempt + 1)

                                        print(

                                                  f"Wikidata 요청 실패: {type(error).__name__}. "

                                                  f"{wait_seconds}초 후 재시도 "

                                                  f"({attempt + 1}/{max_retries})"

                                        )

                                        time.sleep(wait_seconds)

                                        continue


                              raise RuntimeError(

                                        f"Wikidata 요청이 {max_retries}회 시도 후에도 "

                                        "실패했습니다. 잠시 후 다시 실행하세요."

                              ) from error


          raise RuntimeError("Wikidata SPARQL 요청에 실패했습니다.")


def cache_path(qid):

          return CACHE_DIR / f"{qid}.json"


def get_entity_cache(qid):

          path = cache_path(qid)


          if not path.exists():

                    return None


          try:

                    return json.loads(path.read_text(encoding="utf-8"))

          except (json.JSONDecodeError, OSError):

                    return None


def fetch_wikidata_entities(qids):

          CACHE_DIR.mkdir(parents=True, exist_ok=True)


          result = {}

          missing_qids = []


          for qid in dict.fromkeys(qids):

                    if not qid:

                              continue


                    cached = get_entity_cache(qid)


                    if cached is not None:

                              result[qid] = cached

                    else:

                              missing_qids.append(qid)


          for qid_batch in chunks(missing_qids):

                    data = request_json(

                              WIKIDATA_API,

                              {

                                        "action": "wbgetentities",

                                        "format": "json",

                                        "ids": "|".join(qid_batch),

                                        "props": "labels|descriptions|claims",

                                        "languages": "ko|en",

                              },

                    )


                    for qid, entity in data.get("entities", {}).items():

                              result[qid] = entity


                              cache_path(qid).write_text(

                                        json.dumps(

                                                  entity,

                                                  ensure_ascii=False,

                                                  indent=2,

                                        ),

                                        encoding="utf-8",

                              )


                    time.sleep(REQUEST_DELAY_SECONDS)


          return result


def claim_qids(entity, property_id):

          qids = []


          for claim in entity.get("claims", {}).get(property_id, []):

                    snak = claim.get("mainsnak", {})

                    value = snak.get("datavalue", {}).get("value", {})


                    if (

                              snak.get("snaktype") == "value"

                              and value.get("id")

                    ):

                              qids.append(value["id"])


          return list(dict.fromkeys(qids))


def labels_for_qids(qids, entities):

          labels = []


          for qid in qids:

                    entity = entities.get(qid, {})


                    label = (

                              entity.get("labels", {}).get("ko", {}).get("value")

                              or entity.get("labels", {}).get("en", {}).get("value")

                              or qid

                    )

                    labels.append(label)


          return labels
