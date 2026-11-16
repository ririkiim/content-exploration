
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/enrichment/normalization_v2/wikidata_cache"
OUT = ROOT / "exploration_v3/staging/entity"

PROPERTIES = {
    "P4947": "tmdb_movie",
    "P4983": "tmdb_tv",
    "P345": "imdb",
}

IMDB_TITLE = re.compile(r"^tt\d+$")
IMDB_PERSON = re.compile(r"^nm\d+$")
TMDB_NUMERIC = re.compile(r"^\d+$")


def text_value(value):
    if isinstance(value, dict):
        return value.get("value", "")
    return value if isinstance(value, str) else ""


def extract_values(entity, prop):
    result = set()

    for statement in entity.get("claims", {}).get(prop, []):
        if not isinstance(statement, dict):
            continue
        if statement.get("rank") == "deprecated":
            continue

        value = (
            statement.get("mainsnak", {})
            .get("datavalue", {})
            .get("value")
        )

        if isinstance(value, (str, int)):
            result.add(str(value).strip())

    return result


def classify(prop, value):
    if prop in ("P4947", "P4983"):
        return (
            "VALID_NUMERIC"
            if TMDB_NUMERIC.fullmatch(value)
            else "OTHER"
        )

    if IMDB_TITLE.fullmatch(value):
        return "IMDB_TITLE"

    if IMDB_PERSON.fullmatch(value):
        return "IMDB_PERSON"

    return "IMDB_OTHER"


def main():
    qid_ids = defaultdict(lambda: defaultdict(set))
    labels = {}

    files = sorted(CACHE.glob("entities_p31_*.json"))

    for path in files:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        for qid, entity in data.get("entities", {}).items():
            if not isinstance(entity, dict):
                continue

            entity_labels = entity.get("labels", {})

            current = labels.setdefault(qid, {
                "label_ko": "",
                "label_en": "",
            })

            for lang in ("ko", "en"):
                value = text_value(entity_labels.get(lang))
                if value and not current[f"label_{lang}"]:
                    current[f"label_{lang}"] = value

            for prop in PROPERTIES:
                qid_ids[qid][prop].update(
                    extract_values(entity, prop)
                )

    rows = []
    counts = Counter()
    qids_by_property = defaultdict(set)

    for qid, props in sorted(qid_ids.items()):
        for prop, values in props.items():
            for value in sorted(values):
                if not value:
                    continue

                kind = classify(prop, value)

                rows.append({
                    "qid": qid,
                    "property": prop,
                    "id_type": PROPERTIES[prop],
                    "external_id": value,
                    "id_class": kind,
                    **labels.get(qid, {}),
                })

                counts[kind] += 1
                qids_by_property[prop].add(qid)

    OUT.mkdir(parents=True, exist_ok=True)

    with (
        OUT / "14_cached_external_ids.csv"
    ).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "qid", "property", "id_type",
                "external_id", "id_class",
                "label_ko", "label_en",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "cache_files": len(files),
        "total_external_id_rows": len(rows),
        "qids_by_property": {
            prop: len(qids_by_property[prop])
            for prop in PROPERTIES
        },
        "id_class_counts": dict(counts),
        "tmdb_movie_id_rows": sum(
            row["property"] == "P4947"
            and row["id_class"] == "VALID_NUMERIC"
            for row in rows
        ),
        "tmdb_tv_id_rows": sum(
            row["property"] == "P4983"
            and row["id_class"] == "VALID_NUMERIC"
            for row in rows
        ),
        "imdb_title_id_rows": counts["IMDB_TITLE"],
        "imdb_person_id_rows": counts["IMDB_PERSON"],
        "local_work_matches": "NOT_CHECKED",
        "new_api_requests": 0,
        "database_writes": False,
    }

    with (
        OUT / "15_cached_external_id_report.json"
    ).open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== Wikidata 외부 식별자 분석 ===")
    print("TMDb 영화 QID:", len(qids_by_property["P4947"]))
    print("TMDb TV QID:", len(qids_by_property["P4983"]))
    print("IMDb 제목 ID:", counts["IMDB_TITLE"])
    print("IMDb 인물 ID:", counts["IMDB_PERSON"])
    print("기타 IMDb ID:", counts["IMDB_OTHER"])
    print("\n저장 위치:", OUT)


if __name__ == "__main__":
    main()
