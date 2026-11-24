"""Normalize KMDb/TMDb PERSON credits without transliteration or identity merging.

Input:  structured_relations/data/01_screen_person_candidates.jsonl
Output: structured_relations/data/02_screen_person_normalized.jsonl

Read-only, streaming, atomic output replacement. Python 3.10+; stdlib only.
Name comparison keys are CANDIDATE GENERATION signals, not proof of identity.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

STRUCTURED_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = STRUCTURED_ROOT / "data" / "01_screen_person_candidates.jsonl"
DEFAULT_OUTPUT = STRUCTURED_ROOT / "data" / "02_screen_person_normalized.jsonl"

SPACE_RE = re.compile(r"\s+")
HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
LATIN_RE = re.compile(r"[A-Za-z]")
APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"})
HYPHENS = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-", "－": "-"})
# Strip only formatting separators for a *secondary* key, not arbitrary punctuation.
# Keep apostrophes: O'Neil and Oneil must not automatically collapse.
LOOSE_SEPARATORS = re.compile(r"[\s.\-·ㆍ・]+")
VALID_ROLES = {"director", "cast", "screenwriter", "original_author"}
VALID_SOURCES = {"kmdb", "tmdb"}
VALID_TYPES = {"movie", "tv"}


def clean_text(value: object) -> str | None:
    """Conservative display normalization; never translate or reorder tokens."""
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize("NFC", value)
    value = SPACE_RE.sub(" ", value).strip()
    return value or None


def comparison_key(value: object) -> str | None:
    """Case-folded strict comparison key with Unicode punctuation harmonized."""
    value = clean_text(value)
    if value is None:
        return None
    return unicodedata.normalize("NFC", value.translate(APOSTROPHES).translate(HYPHENS)).casefold()


def loose_comparison_key(value: object) -> str | None:
    """Secondary key ignoring spaces, hyphens and periods; NOT identity evidence."""
    key = comparison_key(value)
    if key is None:
        return None
    return LOOSE_SEPARATORS.sub("", key) or None


def script_type(value: str | None) -> str:
    if not value:
        return "missing"
    ko = bool(HANGUL_RE.search(value))
    en = bool(LATIN_RE.search(value))
    if ko and en:
        return "mixed_hangul_latin"
    if ko:
        return "hangul"
    if en:
        return "latin_or_mixed_other"
    return "other"


def clean_identifier(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    result = clean_text(value)
    return None if result is None or result.casefold() in {"null", "none"} else result


def name_variants(name: str | None, original_name: str | None) -> list[dict]:
    """Preserve the provenance of each name spelling for later cross-source matching."""
    variants = []
    seen = set()
    for field, value in (("name", name), ("original_name", original_name)):
        if not value:
            continue
        strict = comparison_key(value)
        loose = loose_comparison_key(value)
        if (strict, loose) in seen:
            continue
        seen.add((strict, loose))
        variants.append({
            "field": field,
            "value": value,
            "script": script_type(value),
            "key": strict,
            "loose_key": loose,
        })
    return variants


def normalize_record(row: dict, line_number: int) -> dict:
    source = row.get("source")
    role = row.get("role")
    content_type = row.get("content_type")
    content_id = row.get("content_id")
    if source not in VALID_SOURCES or role not in VALID_ROLES or content_type not in VALID_TYPES:
        raise ValueError(f"line {line_number}: invalid source/role/content_type: {source}/{role}/{content_type}")
    if isinstance(content_id, bool) or not isinstance(content_id, int) or content_id <= 0:
        raise ValueError(f"line {line_number}: invalid content_id: {content_id!r}")
    if source == "kmdb" and content_type != "movie":
        raise ValueError(f"line {line_number}: KMDb source must be movie")

    name = clean_text(row.get("name"))
    original_name = clean_text(row.get("original_name"))
    if not name and not original_name:
        raise ValueError(f"line {line_number}: both name and original_name missing")

    kmdb_id = clean_identifier(row.get("kmdb_person_id"))
    tmdb_id = clean_identifier(row.get("tmdb_person_id"))
    if source == "kmdb" and tmdb_id is not None:
        raise ValueError(f"line {line_number}: unexpected TMDb person ID on KMDb credit")
    if source == "tmdb" and kmdb_id is not None:
        raise ValueError(f"line {line_number}: unexpected KMDb person ID on TMDb credit")

    out = dict(row)  # Retain every original credit and its evidence untouched.
    out["normalized_name"] = name or original_name
    out["name_key"] = comparison_key(name or original_name)
    out["original_name_key"] = comparison_key(original_name)
    out["name_loose_key"] = loose_comparison_key(name or original_name)
    out["original_name_loose_key"] = loose_comparison_key(original_name)
    out["name_variants"] = name_variants(name, original_name)
    out["name_script"] = script_type(name or original_name)
    out["original_name_script"] = script_type(original_name)
    out["kmdb_person_id_normalized"] = kmdb_id
    out["tmdb_person_id_normalized"] = tmdb_id
    person_id = kmdb_id if source == "kmdb" else tmdb_id
    out["source_person_key"] = f"{source}:{person_id}" if person_id is not None else None
    out["identity_match_status"] = "not_matched"
    out["transliteration_used"] = False
    return out


def run(input_path: Path, output_path: Path, *, skip_invalid: bool) -> Counter:
    if not input_path.is_file():
        raise FileNotFoundError(f"Input JSONL not found: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must differ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats: Counter = Counter()
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", suffix=".tmp",
            prefix=output_path.name + ".", dir=output_path.parent, delete=False
        ) as fout:
            tmp_name = fout.name
            with input_path.open("r", encoding="utf-8-sig") as fin:
                for line_number, line in enumerate(fin, 1):
                    if not line.strip():
                        continue
                    stats["input_rows"] += 1
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError("JSON root must be an object")
                        result = normalize_record(row, line_number)
                    except (ValueError, TypeError, json.JSONDecodeError) as exc:
                        stats["invalid_rows"] += 1
                        if skip_invalid:
                            if stats["invalid_rows"] <= 10:
                                print(f"[WARN] skipped line {line_number}: {exc}")
                            continue
                        raise ValueError(f"Invalid input at line {line_number}: {exc}") from exc
                    fout.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
                    stats["output_rows"] += 1
                    stats[f"{result['source']}_{result['content_type']}_{result['role']}"] += 1
                    stats[f"{result['source']}_{'with' if result['source_person_key'] else 'without'}_person_id"] += 1
                    stats[f"script_{result['name_script']}"] += 1
                    if result["name_loose_key"] != result["name_key"]:
                        stats["name_loose_key_differs"] += 1
                    if result["original_name_loose_key"] != result["original_name_key"]:
                        stats["original_name_loose_key_differs"] += 1
                    if len(result["name_variants"]) > 1:
                        stats["multiple_name_variants"] += 1
        os.replace(tmp_name, output_path)
        tmp_name = None
    finally:
        if tmp_name is not None and os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-invalid", action="store_true", help="Skip malformed rows (default: stop safely)")
    args = parser.parse_args()
    print(f"[INFO] Input: {args.input}", flush=True)
    stats = run(args.input, args.output, skip_invalid=args.skip_invalid)
    print(json.dumps({
        "input": str(args.input), "output": str(args.output),
        "database_changed": False, "transliteration_used": False,
        "identity_merged": False, "cast_final_limit_applied": False,
        "counts": dict(sorted(stats.items())),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
