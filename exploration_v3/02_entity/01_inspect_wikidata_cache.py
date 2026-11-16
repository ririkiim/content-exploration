
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CACHE_DIR = (
    ROOT / "data/enrichment/normalization_v2/wikidata_cache"
)

OUTPUT_DIR = (
    ROOT / "exploration_v3/staging/entity"
)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

entities = {}
duplicate_qids = 0
file_count = 0

for path in sorted(CACHE_DIR.glob("entities_p31_*.json")):
    file_count += 1

    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    for qid, entity in data.get("entities", {}).items():
        if qid in entities:
            duplicate_qids += 1
            continue

        if isinstance(entity, dict) and "missing" not in entity:
            entities[qid] = entity

property_counts = Counter()
label_counts = Counter()
alias_counts = Counter()

for entity in entities.values():
    for lang in ("ko", "en"):
        if entity.get("labels", {}).get(lang):
            label_counts[lang] += 1

        if entity.get("aliases", {}).get(lang):
            alias_counts[lang] += 1

    for prop in entity.get("claims", {}):
        property_counts[prop] += 1

report = {
    "cache_files": file_count,
    "unique_qids": len(entities),
    "duplicate_qid_occurrences": duplicate_qids,
    "labels": dict(label_counts),
    "aliases": dict(alias_counts),
    "property_coverage": dict(
        property_counts.most_common()
    ),
}

output = OUTPUT_DIR / "cache_inspection.json"

with output.open("w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print("캐시 파일:", file_count)
print("고유 QID:", len(entities))
print("중복 QID 등장:", duplicate_qids)
print("한국어 라벨:", label_counts["ko"])
print("영어 라벨:", label_counts["en"])
print("한국어 별칭:", alias_counts["ko"])
print("영어 별칭:", alias_counts["en"])

print("\n주요 관계 속성:")
for prop in (
    "P31", "P279", "P361", "P131",
    "P580", "P582", "P585",
    "P607", "P463", "P710",
):
    count = property_counts[prop]
    print(f"{prop}: {count}")

print(f"\n결과 저장: {output}")
