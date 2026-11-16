
import csv
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

# 1. 경로
ROOT = Path(__file__).resolve().parents[2]
KEYWORDS = ROOT / "exploration_v3/staging/01_keyword_terms.csv"
CACHE = ROOT / "data/enrichment/normalization_v2/wikidata_cache"
OUT = ROOT / "exploration_v3/staging/entity"
OUT.mkdir(parents=True, exist_ok=True)

RELATION_PROPS = ("P279", "P361")
TIME_PROPS = (
    "P2348", "P569", "P570", "P580", "P582",
    "P585", "P27", "P17", "P155", "P156",
)
WORK_PROPS = ("P345", "P921", "P840", "P180")


# 2. 데이터 읽기
def text_value(value):
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def normalize(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(value.casefold().split())


def claim_targets(claims, prop):
    targets = set()

    for statement in claims.get(prop, []):
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
            targets.add(value["id"])

    return targets


def load_cache():
    entities = {}
    duplicates = 0
    differing_duplicates = 0
    files = sorted(CACHE.glob("entities_p31_*.json"))

    if not files:
        raise FileNotFoundError(f"캐시 없음: {CACHE}")

    for path in files:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        for qid, raw in data.get("entities", {}).items():
            if not isinstance(raw, dict) or "missing" in raw:
                continue

            if qid not in entities:
                entities[qid] = {
                    "labels": {},
                    "aliases": defaultdict(set),
                    "descriptions": {},
                    "claims": defaultdict(set),
                    "signatures": set(),
                }
            else:
                duplicates += 1

            entity = entities[qid]

            signature = json.dumps(
                raw, ensure_ascii=False, sort_keys=True
            )
            if entity["signatures"] and signature not in entity["signatures"]:
                differing_duplicates += 1
            entity["signatures"].add(signature)

            for lang in ("ko", "en"):
                label = text_value(
                    raw.get("labels", {}).get(lang)
                )
                if label:
                    entity["labels"].setdefault(lang, label)

                desc = text_value(
                    raw.get("descriptions", {}).get(lang)
                )
                if desc:
                    entity["descriptions"].setdefault(lang, desc)

                for alias in raw.get("aliases", {}).get(lang, []):
                    value = text_value(alias)
                    if value:
                        entity["aliases"][lang].add(value)

            for prop, statements in raw.get("claims", {}).items():
                if not isinstance(statements, list):
                    continue

                for target in claim_targets(
                    {prop: statements}, prop
                ):
                    entity["claims"][prop].add(target)

                # 대상이 QID가 아닌 속성도 보유 여부는 기록
                if statements:
                    entity["claims"][f"_HAS_{prop}"].add("1")

    for entity in entities.values():
        del entity["signatures"]

    return entities, {
        "cache_files": len(files),
        "duplicate_occurrences": duplicates,
        "differing_duplicate_occurrences": differing_duplicates,
    }


# 3. 문자열 인덱스
def build_index(entities):
    index = defaultdict(set)

    for qid, entity in entities.items():
        for lang in ("ko", "en"):
            label = entity["labels"].get(lang)
            if label:
                index[normalize(label)].add(
                    (qid, f"LABEL_{lang.upper()}")
                )

            for alias in entity["aliases"][lang]:
                index[normalize(alias)].add(
                    (qid, f"ALIAS_{lang.upper()}")
                )

    return index


# 4. 매칭 및 후보 생성
def match_keywords(index, entities):
    status_rows = []
    candidate_rows = []
    evidence_counts = Counter()
    p31_counts = Counter()
    short_counts = Counter()
    relation_counts = Counter()

    with KEYWORDS.open(
        encoding="utf-8-sig", newline=""
    ) as f:
        reader = csv.DictReader(f)
        required = {"staging_id", "normalized_term"}

        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                f"키워드 컬럼 확인: {reader.fieldnames}"
            )

        for row in reader:
            term = row["normalized_term"]
            staging_id = row["staging_id"]
            key = normalize(term)

            hits = index.get(key, set())
            grouped = defaultdict(set)

            for qid, evidence in hits:
                grouped[qid].add(evidence)

            qids = sorted(grouped)

            if not qids:
                status = "NONE"
            elif len(qids) == 1:
                status = "CANDIDATE_UNIQUE"
            else:
                status = "CANDIDATE_MULTI"

            # 한글 1~2글자: 제외하지 않고 위험 표시
            hangul_only = bool(key) and all(
                "\uac00" <= ch <= "\ud7a3" for ch in key
            )
            short_flag = (
                "SHORT_KO" if hangul_only and len(key) <= 2
                else ""
            )

            if short_flag:
                short_counts[status] += 1

            status_rows.append({
                "staging_id": staging_id,
                "normalized_term": term,
                "status": status,
                "candidate_count": len(qids),
                "candidate_qids": "|".join(qids),
                "risk_flag": short_flag,
            })

            for qid in qids:
                entity = entities[qid]
                evidence = sorted(grouped[qid])
                types = sorted(entity["claims"].get("P31", set()))

                for ev in evidence:
                    evidence_counts[ev] += 1

                for p31 in types:
                    p31_counts[p31] += 1

                for prop in RELATION_PROPS:
                    targets = entity["claims"].get(prop, set())
                    relation_counts[f"{prop}_targets"] += len(targets)
                    relation_counts[f"{prop}_cached_targets"] += sum(
                        target in entities for target in targets
                    )

                candidate_rows.append({
                    "staging_id": staging_id,
                    "normalized_term": term,
                    "qid": qid,
                    "match_evidence": "|".join(evidence),
                    "label_ko": entity["labels"].get("ko", ""),
                    "label_en": entity["labels"].get("en", ""),
                    "description_ko": entity["descriptions"].get("ko", ""),
                    "description_en": entity["descriptions"].get("en", ""),
                    "p31_types": "|".join(types),
                    "risk_flag": short_flag,
                    "status": "CANDIDATE",
                })

    return (
        status_rows, candidate_rows, evidence_counts,
        p31_counts, short_counts, relation_counts
    )


# 5. 캐시 속성 분석
def inspect_properties(entities):
    props = ("P31", "P279", "P361") + TIME_PROPS + WORK_PROPS
    coverage = {}

    for prop in props:
        coverage[prop] = sum(
            bool(e["claims"].get(f"_HAS_{prop}"))
            for e in entities.values()
        )

    relation = {}

    for prop in RELATION_PROPS:
        sources = 0
        sources_with_cached_target = 0
        target_count = 0
        cached_target_count = 0

        for entity in entities.values():
            targets = entity["claims"].get(prop, set())
            if not targets:
                continue

            sources += 1
            cached = sum(t in entities for t in targets)
            target_count += len(targets)
            cached_target_count += cached

            if cached:
                sources_with_cached_target += 1

        relation[prop] = {
            "source_qids": sources,
            "sources_with_cached_target": sources_with_cached_target,
            "unique_source_target_pairs": target_count,
            "pairs_with_cached_target": cached_target_count,
            "cached_target_percent": round(
                cached_target_count / target_count * 100, 2
            ) if target_count else 0,
        }

    return coverage, relation


# 6. 결과 저장
def save_csv(filename, rows, columns):
    with (OUT / filename).open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    entities, cache_info = load_cache()
    index = build_index(entities)

    (
        status_rows, candidates, evidence_counts,
        p31_counts, short_counts, matched_relation_counts
    ) = match_keywords(index, entities)

    property_coverage, relation_coverage = inspect_properties(
        entities
    )

    counts = Counter(row["status"] for row in status_rows)
    total = len(status_rows)
    discovered = total - counts["NONE"]

    report = {
        "interpretation": (
            "기존 조회 후보 캐시의 오프라인 재발견률. "
            "전체 Wikidata 커버리지 또는 검증된 매핑률이 아님."
        ),
        "total_keywords": total,
        "cached_qids": len(entities),
        "candidate_found_keywords": discovered,
        "cache_rediscovery_percent": round(
            discovered / total * 100, 2
        ) if total else 0,
        "status_counts": dict(counts),
        "candidate_qid_rows": len(candidates),
        "match_evidence_counts": dict(evidence_counts),
        "short_korean_keyword_counts": dict(short_counts),
        "candidate_p31_type_distribution": dict(
            p31_counts.most_common()
        ),
        "matched_candidate_relation_counts": dict(
            matched_relation_counts
        ),
        "cache_property_coverage": property_coverage,
        "cache_relation_target_coverage": relation_coverage,
        "cache_info": cache_info,
    }

    save_csv(
        "02_keyword_match_status.csv",
        status_rows,
        [
            "staging_id", "normalized_term", "status",
            "candidate_count", "candidate_qids", "risk_flag",
        ],
    )

    save_csv(
        "03_wikidata_candidates.csv",
        candidates,
        [
            "staging_id", "normalized_term", "qid",
            "match_evidence", "label_ko", "label_en",
            "description_ko", "description_en",
            "p31_types", "risk_flag", "status",
        ],
    )

    with (OUT / "04_match_report.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== 캐시 재발견 분석 ===")
    print("전체 키워드:", total)
    print("캐시 QID:", len(entities))
    print("후보 발견:", discovered)
    print("재발견률:", f'{report["cache_rediscovery_percent"]}%')
    print("단일 후보:", counts["CANDIDATE_UNIQUE"])
    print("복수 후보:", counts["CANDIDATE_MULTI"])
    print("후보 없음:", counts["NONE"])
    print("후보 QID 행:", len(candidates))

    print("\n=== 관계 대상 캐시 존재율 ===")
    for prop, stats in relation_coverage.items():
        print(
            prop,
            f'{stats["pairs_with_cached_target"]}/'
            f'{stats["unique_source_target_pairs"]}',
            f'({stats["cached_target_percent"]}%)',
        )

    print("\n=== 시기 관련 속성 보유 QID ===")
    for prop in TIME_PROPS:
        print(prop, property_coverage[prop])

    print("\n=== 작품 관련 속성 보유 QID ===")
    for prop in WORK_PROPS:
        print(prop, property_coverage[prop])

    print("\n=== 캐시 중복 ===")
    print(cache_info)
    print("\n결과:", OUT)


if __name__ == "__main__":
    main()
