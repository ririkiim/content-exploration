"""기존 평가 코드의 30개 목록을 부작용 없이 재사용한다."""
import ast
import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from ..wikidata import config


def load_targets():
    path = Path(__file__).resolve().parents[1] / "LLM" / "load_content_topics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TEST_CONTENTS" for t in node.targets):
            targets = ast.literal_eval(node.value)
            if not isinstance(targets, list) or len(set(targets)) != len(targets):
                raise ValueError("대상 목록 형식을 확인하세요.")
            return targets
    raise ValueError("기존 TEST_CONTENTS 목록을 찾지 못했습니다.")


def resolve_targets(targets):
    output = []
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            for kind, title in targets:
                cur.execute("SELECT content_id, title, content_type FROM content WHERE content_type=%s AND title=%s ORDER BY content_id", (kind, title))
                rows = cur.fetchall()
                output.append({"requested_title": title, "content_type": kind,
                               "resolution": "matched" if len(rows) == 1 else "ambiguous" if rows else "not_found",
                               "content_id": rows[0]["content_id"] if len(rows) == 1 else None,
                               "candidate_ids": [row["content_id"] for row in rows]})
    return output
