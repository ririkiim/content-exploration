"""QID 단위 원본 캐시. 성공한 API 원본은 한 번 저장하고 그대로 재사용한다."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from ..wikidata.client import get_entities


def check_qid(qid):
    if not isinstance(qid, str) or not re.fullmatch(r"Q[1-9]\d*", qid):
        raise ValueError(f"잘못된 QID: {qid}")
    return qid


class EntityStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.memory = {}
        self.stats = {"requested_qids": 0, "cached_qids": 0}

    def get_many(self, qids):
        qids = sorted({check_qid(qid) for qid in qids})
        pending = []
        for qid in qids:
            if qid in self.memory:
                continue
            path = self.folder / f"{qid}.json"
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["entity"].get("id") != qid:
                    raise ValueError(f"캐시 QID 불일치: {qid}")
                self.memory[qid] = record
                self.stats["cached_qids"] += 1
            else:
                pending.append(qid)
        if pending:
            entities = get_entities(pending)
            self.stats["requested_qids"] += len(pending)
            for qid in pending:
                entity = entities.get(qid)
                if entity and entity.get("id") != qid:
                    raise ValueError(f"API QID 불일치: {qid}")
                record = {"entity": entity, "fetched_at": datetime.now(timezone.utc).isoformat()}
                self.memory[qid] = record
                if entity:
                    # 캐시 원본을 갱신하려면 다른 cache-dir를 사용한다.
                    with (self.folder / f"{qid}.json").open("x", encoding="utf-8") as file:
                        json.dump(record, file, ensure_ascii=False, indent=2)
        return {qid: self.memory[qid]["entity"] for qid in qids}

    def get(self, qid):
        return self.get_many([qid])[qid]

    def provenance(self, qid):
        self.get(qid)
        return {"snapshot_path": str((self.folder / f"{qid}.json").resolve()),
                "fetched_at": self.memory[qid]["fetched_at"]}
