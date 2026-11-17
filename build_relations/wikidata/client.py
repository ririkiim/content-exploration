import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import API_URL, REQUEST_DELAY, SPARQL_URL, USER_AGENT


def request_json(url, params, post=False):
    encoded = urlencode(params).encode("utf-8")

    for attempt in range(3):
        request = Request(
            url if post else f"{url}?{encoded.decode('utf-8')}",
            data=encoded if post else None,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=60) as response:
                data = json.load(response)

            if "error" in data:
                raise RuntimeError(str(data["error"]))

            return data

        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504}:
                raise
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))

        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))

        finally:
            time.sleep(REQUEST_DELAY)


def lookup_id(property_id, value):
    if property_id not in {"P4947", "P4983", "P212"}:
        raise ValueError(f"지원하지 않는 속성: {property_id}")

    if property_id == "P212":
        value = re.sub(r"[^0-9Xx]", "", str(value)).upper()
        if not value:
            return []

        pattern = f"""
            ?item wdt:P212 ?isbn .
            FILTER(
                UCASE(REPLACE(STR(?isbn), "[^0-9Xx]", ""))
                = {json.dumps(value)}
            )
        """
    else:
        pattern = (
            f"?item wdt:{property_id} "
            f"{json.dumps(str(value))} ."
        )

    data = request_json(
        SPARQL_URL,
        {
            "query": f"SELECT DISTINCT ?item WHERE {{ {pattern} }}",
            "format": "json",
        },
        post=True,
    )

    return sorted({
        row["item"]["value"].rsplit("/", 1)[-1]
        for row in data["results"]["bindings"]
    })


def search_title(title):
    qids = set()

    for language in ("ko", "en"):
        data = request_json(
            API_URL,
            {
                "action": "wbsearchentities",
                "format": "json",
                "search": title,
                "language": language,
                "type": "item",
                "limit": 20,
            },
        )

        qids.update(
            item["id"]
            for item in data.get("search", [])
            if re.fullmatch(r"Q\d+", item.get("id", ""))
        )

    return sorted(qids)


def get_entities(qids):
    qids = sorted(set(qids))
    if any(not re.fullmatch(r"Q\d+", qid) for qid in qids):
        raise ValueError("잘못된 QID입니다.")

    entities = {}

    for start in range(0, len(qids), 40):
        data = request_json(
            API_URL,
            {
                "action": "wbgetentities",
                "format": "json",
                "ids": "|".join(qids[start:start + 40]),
                "props": "labels|aliases|descriptions|claims",
                "languages": "ko|en",
            },
        )
        entities.update(data.get("entities", {}))

    return {
        qid: entity
        for qid, entity in entities.items()
        if "missing" not in entity
    }


def claim_values(entity, property_id):
    values = []

    for claim in entity.get("claims", {}).get(property_id, []):
        if claim.get("rank") == "deprecated":
            continue

        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            value = snak.get("datavalue", {}).get("value")
            if value is not None:
                values.append(value)

    return values


def claim_qids(entity, property_id):
    return sorted({
        value["id"]
        for value in claim_values(entity, property_id)
        if isinstance(value, dict) and value.get("id")
    })


def label(entity):
    labels = entity.get("labels", {})
    return (
        labels.get("ko", {}).get("value")
        or labels.get("en", {}).get("value")
        or entity.get("id")
    )


def names(entity):
    result = set()

    for language in ("ko", "en"):
        name = entity.get("labels", {}).get(language, {}).get("value")
        if name:
            result.add(name)

        result.update(
            alias["value"]
            for alias in entity.get("aliases", {}).get(language, [])
            if alias.get("value")
        )

    return result