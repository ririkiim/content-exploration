
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "exploration_v3/staging/entity"


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def get_title_map(path, id_col, title_col):
    if not path:
        return {}

    rows = read_csv(path)
    if rows and (
        id_col not in rows[0] or title_col not in rows[0]
    ):
        raise ValueError(
            f"제목 파일에 {id_col}, {title_col} 컬럼이 필요합니다. "
            f"실제 컬럼: {list(rows[0])}"
        )

    titles = defaultdict(set)
    for row in rows:
        cid = row[id_col].strip()
        title = row[title_col].strip()
        if cid and title:
            titles[cid].add(title)

    return {
        cid: " | ".join(sorted(values))
        for cid, values in titles.items()
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--titles", type=Path)
    parser.add_argument("--id-col", default="content_id")
    parser.add_argument("--title-col", default="title")
    args = parser.parse_args()

    contribution = read_csv(
        OUT / "16_relation_contribution.csv"
    )
    samples = read_csv(
        OUT / "17_top_relation_samples.csv"
    )
    term_edges = read_csv(
        OUT / "12_strict_relation_term_pairs.csv"
    )
    qid_reviews = read_csv(
        OUT / "09_relation_quality_review.csv"
    )

    title_map = get_title_map(
        args.titles, args.id_col, args.title_col
    )

    # 키워드 쌍 → QID 관계 근거
    edge_evidence = {}

    for row in term_edges:
        key = (
            row["source_staging_id"],
            row["target_staging_id"],
        )
        edge_evidence[key] = json.loads(row["evidence"])

    # QID 관계 → 기존 캐시 분석 메타데이터
    qid_info = defaultdict(list)

    for row in qid_reviews:
        key = (
            row["source_qid"],
            row["property"],
            row["target_qid"],
        )
        qid_info[key].append(row)

    # 상위 30개 관계 검토표
    review_rows = []
    missing_metadata = 0

    for edge in contribution[:30]:
        key = (
            edge["source_staging_id"],
            edge["target_staging_id"],
        )

        evidence = edge_evidence.get(key, [])

        for item in evidence:
            qkey = (
                item["source_qid"],
                item["property"],
                item["target_qid"],
            )

            matches = qid_info.get(qkey, [])
            info = matches[0] if matches else {}

            if not matches:
                missing_metadata += 1

            review_rows.append({
                "rank": edge["rank"],
                "source_term": edge["source_term"],
                "target_term": edge["target_term"],
                "source_qid": item["source_qid"],
                "property": item["property"],
                "target_qid": item["target_qid"],
                "source_label_ko": info.get(
                    "source_label_ko", ""
                ),
                "source_label_en": info.get(
                    "source_label_en", ""
                ),
                "source_description_ko": info.get(
                    "source_description_ko", ""
                ),
                "source_description_en": info.get(
                    "source_description_en", ""
                ),
                "source_p31": info.get("source_p31", ""),
                "target_label_ko": info.get(
                    "target_label_ko", ""
                ),
                "target_label_en": info.get(
                    "target_label_en", ""
                ),
                "target_description_ko": info.get(
                    "target_description_ko", ""
                ),
                "target_description_en": info.get(
                    "target_description_en", ""
                ),
                "target_p31": info.get("target_p31", ""),
                "a0_new_pairs": edge["a0_new_pairs"],
                "incremental_new_pairs": edge[
                    "incremental_new_pairs"
                ],
                "qid_mapping_validity": "",
                "relation_validity": "",
                "exploration_usefulness": "",
                "suggested_relation": "",
                "review_notes": "",
            })

    review_columns = [
        "rank", "source_term", "target_term",
        "source_qid", "property", "target_qid",
        "source_label_ko", "source_label_en",
        "source_description_ko", "source_description_en",
        "source_p31",
        "target_label_ko", "target_label_en",
        "target_description_ko", "target_description_en",
        "target_p31",
        "a0_new_pairs", "incremental_new_pairs",
        "qid_mapping_validity", "relation_validity",
        "exploration_usefulness", "suggested_relation",
        "review_notes",
    ]

    write_csv(
        OUT / "19_top_relation_qid_review.csv",
        review_rows,
        review_columns,
    )

    # 작품 사례: 원본 방향을 보존하기 위해
    # source/target 키워드의 작품 소속을 다시 확인
    mappings = read_csv(
        ROOT / "exploration_v3/staging/02_keyword_term_map.csv"
    )

    term_contents = defaultdict(set)
    for row in mappings:
        if row["staging_id"] and row["content_id"]:
            term_contents[row["staging_id"]].add(
                row["content_id"]
            )

    ranked_edges = {
        str(row["rank"]): row
        for row in contribution[:30]
    }

    sample_rows = []

    for sample in samples:
        edge = ranked_edges.get(str(sample["rank"]))
        if not edge:
            continue

        a = sample["content_id_a"]
        b = sample["content_id_b"]

        source_contents = term_contents[
            edge["source_staging_id"]
        ]
        target_contents = term_contents[
            edge["target_staging_id"]
        ]

        # 기존 샘플은 무방향 작품 쌍이므로
        # 실제 source → target 방향을 복원
        orientations = []
        if a in source_contents and b in target_contents:
            orientations.append((a, b))
        if b in source_contents and a in target_contents:
            orientations.append((b, a))

        for source_id, target_id in orientations:
            sample_rows.append({
                "rank": sample["rank"],
                "source_term": edge["source_term"],
                "target_term": edge["target_term"],
                "properties": edge["properties"],
                "source_content_id": source_id,
                "source_title": title_map.get(source_id, ""),
                "target_content_id": target_id,
                "target_title": title_map.get(target_id, ""),
                "review_validity": "",
                "review_usefulness": "",
                "notes": "",
            })

    sample_columns = [
        "rank", "source_term", "target_term",
        "properties", "source_content_id", "source_title",
        "target_content_id", "target_title",
        "review_validity", "review_usefulness", "notes",
    ]

    write_csv(
        OUT / "20_top_relation_work_examples.csv",
        sample_rows,
        sample_columns,
    )

    report = {
        "top_edges": min(30, len(contribution)),
        "qid_evidence_review_rows": len(review_rows),
        "missing_qid_metadata_rows": missing_metadata,
        "work_example_rows": len(sample_rows),
        "titles_supplied": args.titles is not None,
        "database_writes": False,
        "new_api_requests": 0,
    }

    with (
        OUT / "21_relation_review_report.json"
    ).open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== 상위 관계 검토표 ===")
    print("상위 키워드 간선:", report["top_edges"])
    print("QID 근거 검토 행:", len(review_rows))
    print("메타데이터 없는 행:", missing_metadata)
    print("작품 예시 행:", len(sample_rows))
    print("작품 제목 연결:", bool(args.titles))
    print("저장 위치:", OUT)


if __name__ == "__main__":
    main()
