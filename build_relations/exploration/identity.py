"""식별자는 합치고 이름만 같은 대상은 합치지 않는다."""
import hashlib
import re
import unicodedata


def normalize(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value)).casefold())


def stable_id(*parts):
    return hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()[:20]


def merge_assertions(records):
    merged = {}
    for record in records:
        target = record["target"]
        identifiers = target.get("external_ids", {})
        # ID가 없으면 원본 출처와 레코드 범위도 같아야 통합한다.
        identity = tuple(sorted(identifiers.items())) if identifiers else (
            record["identity_scope"], normalize(target["name"]), target["kind"],
        )
        key = (record["relation"], identity)
        if key not in merged:
            merged[key] = {**record, "evidence": list(record["evidence"])}
        else:
            for evidence in record["evidence"]:
                if evidence not in merged[key]["evidence"]:
                    merged[key]["evidence"].append(evidence)
    return list(merged.values())
