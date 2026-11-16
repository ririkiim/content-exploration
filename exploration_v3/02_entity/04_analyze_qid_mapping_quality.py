 
import csv
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

STAGING = ROOT / "exploration_v3/staging"
ENTITY_DIR = STAGING / "entity"
CACHE_DIR = ROOT / "data/enrichment/normalization_v2/wikidata_cache"

STATUS_PATH = ENTITY_DIR / "02_keyword_match_status.csv"
CANDIDATE_PATH = ENTITY_DIR / "03_wikidata_candidates.csv"
RELATION_PATH = ENTITY_DIR / "06_candidate_relations.csv"
MAP_PATH = STAGING / "02_keyword_term_map.csv"

RANDOM_SEED = 42
SAMPLE_PER_STRATUM = 25
HUB_CONTENT_THRESHOLD = 100

# 자동 제외가 아니라 검토 대상 표시용
META_TERMS = {
    "영화", "작품", "드라마", "도서", "책",
    "film", "movie", "work", "book",
    "human", "person", "organization",
    "사람", "인간", "조직",
}

random.seed(RANDOM_SEED)


def load_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def text_value(value):
    if isinstance(value, dict):
        return value.get("value", "")
    return value if isinstance(value, str) else ""


def load_qid_metadata():
    metadata = {}

    for path in sorted(CACHE_DIR.glob("entities_p31_*.json")):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        for qid, entity in data.get("entities", {}).items():
            if not isinstance(entity, dict):
                continue

            item = metadata.setdefault(qid, {
                "label_ko": "",
                "label_en": "",
                "description_ko": "",
                "description_en": "",
                "p31": set(),
            })

            labels = entity.get("labels", {})
            descriptions = entity.get("descriptions", {})

            for lang in ("ko", "en"):
                label = text_value(labels.get(lang))
                description = text_value(descriptions.get(lang))

                if label and not item[f"label_{lang}"]:
                    item[f"label_{lang}"] = label

                if description and not item[f"description_{lang}"]:
                    item[f"description_{lang}"] = description

            for statement in entity.get("claims", {}).get("P31", []):
                if not isinstance(statement, dict):
                    continue

                if statement.get("rank") == "deprecated":
                    continue

                value = (
                    statement.get("mainsnak", {})
                    .get("datavalue", {})
                    .get("value")
                )

                if isinstance(value, dict) and value.get("id"):
                    item["p31"].add(value["id"])

    return metadata


def main():
    ENTITY_DIR.mkdir(parents=True, exist_ok=True)

    statuses = load_csv(STATUS_PATH)
    candidates = load_csv(CANDIDATE_PATH)
    relations = load_csv(RELATION_PATH)
    mappings = load_csv(MAP_PATH)

    qid_metadata = load_qid_metadata()

    status_by_id = {
        row["staging_id"]: row
        for row in statuses
    }

    term_contents = defaultdict(set)

    for row in mappings:
        sid = row["staging_id"]
        cid = row["content_id"]

        if sid and cid:
            term_contents[sid].add(cid)

    qid_terms = defaultdict(set)
    qid_candidate_rows = defaultdict(list)
    term_qids = defaultdict(set)

    for row in candidates:
        sid = row["staging_id"]
        qid = row["qid"]

        qid_terms[qid].add(sid)
        qid_candidate_rows[qid].append(row)
        term_qids[sid].add(qid)

    # ------------------------------------
    # 1. 작품 수 기준 매칭 현황
    # ------------------------------------
    status_content_sets = defaultdict(set)
    status_term_counts = Counter()

    for sid, row in status_by_id.items():
        status = row["status"]
        status_term_counts[status] += 1
        status_content_sets[status].update(
            term_contents.get(sid, set())
        )

    all_contents = set()
    for contents in term_contents.values():
        all_contents.update(contents)

    unique_contents = status_content_sets["CANDIDATE_UNIQUE"]
    multi_contents = status_content_sets["CANDIDATE_MULTI"]

    coverage = {
        "total_terms": len(statuses),
        "total_contents_with_keywords": len(all_contents),
        "status_term_counts": dict(status_term_counts),
        "status_content_counts": {
            key: len(value)
            for key, value in status_content_sets.items()
        },
        "contents_with_unique_candidate": len(unique_contents),
        "contents_with_multi_candidate": len(multi_contents),
        "contents_with_any_candidate": len(
            unique_contents | multi_contents
        ),
    }

    # ------------------------------------
    # 2. QID별 연결 키워드 분석
    # ------------------------------------
    qid_rows = []

    for qid, sids in qid_terms.items():
        terms = sorted({
            status_by_id[sid]["normalized_term"]
            for sid in sids
            if sid in status_by_id
        })

        contents = set()
        unique_count = 0
        multi_count = 0

        for sid in sids:
            contents.update(term_contents.get(sid, set()))

            status = status_by_id.get(sid, {}).get("status")

            if status == "CANDIDATE_UNIQUE":
                unique_count += 1
            elif status == "CANDIDATE_MULTI":
                multi_count += 1

        info = qid_metadata.get(qid, {})

        qid_rows.append({
            "qid": qid,
            "label_ko": info.get("label_ko", ""),
            "label_en": info.get("label_en", ""),
            "description_ko": info.get("description_ko", ""),
            "description_en": info.get("description_en", ""),
            "p31_types": "|".join(sorted(info.get("p31", set()))),
            "keyword_count": len(sids),
            "unique_candidate_terms": unique_count,
            "multi_candidate_terms": multi_count,
            "content_count": len(contents),
            "terms": "|".join(terms),
            "review": "",
        })

    qid_rows.sort(
        key=lambda row: (
            -row["keyword_count"],
            -row["content_count"],
            row["qid"],
        )
    )

    write_csv(
        ENTITY_DIR / "08_qid_keyword_groups.csv",
        qid_rows,
        [
            "qid", "label_ko", "label_en",
            "description_ko", "description_en",
            "p31_types", "keyword_count",
            "unique_candidate_terms", "multi_candidate_terms",
            "content_count", "terms", "review",
        ],
    )

    # ------------------------------------
    # 3. 관계별 실제 QID 메타데이터
    # ------------------------------------
    relation_rows = []
    relation_flags = Counter()
    relation_p31_pairs = Counter()

    for row in relations:
        source = row["source_qid"]
        target = row["target_qid"]
        prop = row["property"]

        source_info = qid_metadata.get(source, {})
        target_info = qid_metadata.get(target, {})

        source_terms = [
            status_by_id[sid]["normalized_term"]
            for sid in qid_terms.get(source, set())
            if sid in status_by_id
        ]

        target_terms = [
            status_by_id[sid]["normalized_term"]
            for sid in qid_terms.get(target, set())
            if sid in status_by_id
        ]

        source_count = int(row["source_content_count"])
        target_count = int(row["target_content_count"])

        flags = []

        if row["skip_reason"] == "HUB_LIMIT":
            flags.append("HUB")

        if (
            source_count > HUB_CONTENT_THRESHOLD
            or target_count > HUB_CONTENT_THRESHOLD
        ):
            flags.append("HIGH_CONTENT_COUNT")

        if any(
            normalize(term) in META_TERMS
            for term in source_terms + target_terms
        ):
            flags.append("META_TERM")

        if set(source_terms) & set(target_terms):
            flags.append("OVERLAPPING_TERMS")

        if any(
            status_by_id[sid]["status"] == "CANDIDATE_MULTI"
            for sid in (
                qid_terms.get(source, set())
                | qid_terms.get(target, set())
            )
            if sid in status_by_id
        ):
            flags.append("MULTI_CANDIDATE")

        for flag in flags:
            relation_flags[flag] += 1

        source_p31 = source_info.get("p31", set())
        target_p31 = target_info.get("p31", set())

        for a in source_p31 or {"UNKNOWN"}:
            for b in target_p31 or {"UNKNOWN"}:
                relation_p31_pairs[(prop, a, b)] += 1

        relation_rows.append({
            "source_qid": source,
            "property": prop,
            "target_qid": target,
            "source_terms": "|".join(sorted(source_terms)),
            "target_terms": "|".join(sorted(target_terms)),
            "source_label_ko": source_info.get("label_ko", ""),
            "source_label_en": source_info.get("label_en", ""),
            "source_description_ko": source_info.get(
                "description_ko", ""
            ),
            "source_description_en": source_info.get(
                "description_en", ""
            ),
            "source_p31": "|".join(sorted(source_p31)),
            "target_label_ko": target_info.get("label_ko", ""),
            "target_label_en": target_info.get("label_en", ""),
            "target_description_ko": target_info.get(
                "description_ko", ""
            ),
            "target_description_en": target_info.get(
                "description_en", ""
            ),
            "target_p31": "|".join(sorted(target_p31)),
            "source_content_count": source_count,
            "target_content_count": target_count,
            "potential_cross_content_pairs": row[
                "potential_cross_content_pairs"
            ],
            "flags": "|".join(flags),
            "relation_validity": "",
            "relation_usefulness": "",
            "suggested_type": "",
            "review_notes": "",
        })

    relation_rows.sort(
        key=lambda row: -int(
            row["potential_cross_content_pairs"]
        )
    )

    write_csv(
        ENTITY_DIR / "09_relation_quality_review.csv",
        relation_rows,
        list(relation_rows[0].keys()) if relation_rows else [
            "source_qid", "property", "target_qid"
        ],
    )

    # ------------------------------------
    # 4. 단일 후보 층화 표본
    # ------------------------------------
    unique_candidates = []

    for row in candidates:
        sid = row["staging_id"]

        if (
            status_by_id.get(sid, {}).get("status")
            != "CANDIDATE_UNIQUE"
        ):
            continue

        unique_candidates.append({
            **row,
            "content_count": len(term_contents.get(sid, set())),
        })

    # 작품 수 기준 3개 층
    strata = {
        "LOW": [],
        "MEDIUM": [],
        "HIGH": [],
    }

    for row in unique_candidates:
        count = row["content_count"]

        if count <= 2:
            strata["LOW"].append(row)
        elif count <= 10:
            strata["MEDIUM"].append(row)
        else:
            strata["HIGH"].append(row)

    sample_rows = []

    for stratum, rows in strata.items():
        selected = random.sample(
            rows,
            min(SAMPLE_PER_STRATUM, len(rows))
        )

        for row in selected:
            sample_rows.append({
                "stratum": stratum,
                "staging_id": row["staging_id"],
                "term": row["normalized_term"],
                "qid": row["qid"],
                "content_count": row["content_count"],
                "label_ko": row.get("label_ko", ""),
                "label_en": row.get("label_en", ""),
                "description_ko": row.get(
                    "description_ko", ""
                ),
                "description_en": row.get(
                    "description_en", ""
                ),
                "p31_types": row.get("p31_types", ""),
                "validity": "",
                "usefulness": "",
                "notes": "",
            })

    write_csv(
        ENTITY_DIR / "10_stratified_candidate_sample.csv",
        sample_rows,
        [
            "stratum", "staging_id", "term", "qid",
            "content_count", "label_ko", "label_en",
            "description_ko", "description_en",
            "p31_types", "validity", "usefulness", "notes",
        ],
    )

    # ------------------------------------
    # 5. 보고서
    # ------------------------------------
    report = {
        "interpretation": (
            "오프라인 QID 후보 품질 진단. "
            "QID 연결은 검증된 SAME이 아니며, "
            "관계도 검증 전 후보임."
        ),
        "coverage": coverage,
        "qid_group_stats": {
            "distinct_qids": len(qid_rows),
            "qids_with_multiple_terms": sum(
                row["keyword_count"] > 1
                for row in qid_rows
            ),
            "qids_with_multi_candidates": sum(
                row["multi_candidate_terms"] > 0
                for row in qid_rows
            ),
        },
        "relation_stats": {
            "total": len(relation_rows),
            "properties": dict(Counter(
                row["property"]
                for row in relation_rows
            )),
            "flags": dict(relation_flags),
        },
        "stratified_sample": {
            key: {
                "population": len(rows),
                "sampled": min(
                    SAMPLE_PER_STRATUM, len(rows)
                ),
            }
            for key, rows in strata.items()
        },
        "p31_pair_top20": [
            {
                "property": prop,
                "source_p31": a,
                "target_p31": b,
                "count": count,
            }
            for (prop, a, b), count in
            relation_p31_pairs.most_common(20)
        ],
        "precision": "NOT_EVALUATED",
        "database_writes": False,
    }

    with (
        ENTITY_DIR / "11_qid_mapping_quality_report.json"
    ).open("w", encoding="utf-8") as f:
        json.dump(
            report, f,
            ensure_ascii=False,
            indent=2
        )

    print("\n=== QID 매칭 품질 진단 ===")
    print("전체 키워드:", len(statuses))
    print("전체 작품:", len(all_contents))
    print("단일 후보 작품:", len(unique_contents))
    print("복수 후보 작품:", len(multi_contents))
    print("후보 존재 작품:", len(unique_contents | multi_contents))
    print("서로 다른 QID:", len(qid_rows))
    print(
        "복수 키워드 연결 QID:",
        report["qid_group_stats"]["qids_with_multiple_terms"]
    )
    print("관계 후보:", len(relation_rows))
    print("관계 위험 플래그:", dict(relation_flags))
    print(
        "층화 표본:",
        {k: len(v) for k, v in strata.items()}
    )
    print("\n저장 위치:", ENTITY_DIR)


if __name__ == "__main__":
    main()
