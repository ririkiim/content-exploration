import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

try:
    from .expansion_prompt import (
        PROMPT_VERSION,
        RELATION_TYPES,
        build_expansion_messages,
    )
except ImportError:
    from expansion_prompt import (
        PROMPT_VERSION,
        RELATION_TYPES,
        build_expansion_messages,
    )


ROOT = Path(__file__).resolve().parents[2]

INPUT_PATH = ROOT / "data/enrichment/expansion/input.json"
OUTPUT_ROOT = ROOT / "data/enrichment/expansion/results"

DEFAULT_MODEL = "gpt-5.6"

VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_KNOWLEDGE_BASIS = {
    "general_knowledge",
    "contextual_inference",
}


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def select_topic(data, topic_id):
    matches = [
        topic
        for topic in data["topics"]
        if topic["temporary_topic_id"] == topic_id
    ]

    if len(matches) != 1:
        raise ValueError(
            f"토픽을 정확히 하나 찾을 수 없습니다: {topic_id}"
        )

    return matches[0]


def validate_result(result, source_topic):
    if not isinstance(result, dict):
        raise ValueError("LLM 결과는 JSON 객체여야 합니다.")

    if result.get("source_topic_id") != source_topic["temporary_topic_id"]:
        raise ValueError("source_topic_id가 입력과 다릅니다.")

    if result.get("source_topic") != source_topic["canonical_name"]:
        raise ValueError("source_topic이 입력과 다릅니다.")

    expansions = result.get("expansions")

    if not isinstance(expansions, list):
        raise ValueError("expansions는 배열이어야 합니다.")

    if len(expansions) > 5:
        raise ValueError("확장 후보가 5개를 초과했습니다.")

    seen = set()

    for index, item in enumerate(expansions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{index}번 후보가 객체가 아닙니다.")

        name = item.get("target_topic")
        reason = item.get("reason")

        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{index}번 후보의 이름이 비어 있습니다.")

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{index}번 후보의 설명이 비어 있습니다.")

        if item.get("relation_type") not in RELATION_TYPES:
            raise ValueError(f"{index}번 후보의 관계 유형이 잘못됐습니다.")

        if item.get("confidence") not in VALID_CONFIDENCE:
            raise ValueError(f"{index}번 후보의 신뢰도가 잘못됐습니다.")

        if item.get("knowledge_basis") not in VALID_KNOWLEDGE_BASIS:
            raise ValueError(f"{index}번 후보의 지식 구분이 잘못됐습니다.")

        key = re.sub(r"\s+", " ", name).strip().casefold()

        source_key = re.sub(
            r"\s+", " ",
            source_topic["canonical_name"],
        ).strip().casefold()

        if key == source_key:
            raise ValueError(f"{index}번 후보가 출발 토픽과 동일합니다.")

        if key in seen:
            raise ValueError(f"중복 확장 후보: {name}")

        seen.add(key)


def call_llm(messages, model):
    client = OpenAI()

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("LLM 응답이 비어 있습니다.")

    result = json.loads(content)

    usage = response.usage

    usage_data = {
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
        "total_tokens": usage.total_tokens if usage else None,
    }

    return result, usage_data


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--topic-id",
        required=True,
        help="예: T0004",
    )

    parser.add_argument(
        "--run",
        action="store_true",
        help="실제 LLM API 호출",
    )

    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
    )

    args = parser.parse_args()

    data = load_json(INPUT_PATH)
    topic = select_topic(data, args.topic_id)

    messages = build_expansion_messages(topic)

    print("\n===== 관계 확장 대상 =====")
    print(f"토픽 ID: {topic['temporary_topic_id']}")
    print(f"토픽명: {topic['canonical_name']}")
    print(f"콘텐츠 ID: {topic['content_ids']}")
    print(f"검토 필요: {topic.get('needs_review', False)}")
    print(f"근거 수: {len(topic['evidence_items'])}")
    print(f"프롬프트: {PROMPT_VERSION}")
    print(f"모델: {args.model}")

    if not args.run:
        print("\n[DRY RUN]")
        print("입력 준비 완료. API는 호출하지 않았습니다.")
        print("실제 실행하려면 --run을 추가하세요.")
        return

    load_dotenv(ROOT / ".env")

    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError(
            "OPENAI_API_KEY를 찾을 수 없습니다."
        )

    result, usage = call_llm(messages, args.model)

    timestamp = datetime.now().astimezone().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    output_dir = OUTPUT_ROOT / args.topic_id / timestamp
    output_path = output_dir / "result.json"

    validation_errors = []

    try:
        validate_result(result, topic)
    except ValueError as exc:
        validation_errors.append(str(exc))

    output = {
        "created_at": datetime.now().astimezone().isoformat(),
        "prompt_version": PROMPT_VERSION,
        "model": args.model,
        "source_topic_id": args.topic_id,
        "source_topic": topic["canonical_name"],
        "validation_passed": not validation_errors,
        "validation_errors": validation_errors,
        "usage": usage,
        "result": result,
    }

    output_dir.mkdir(parents=True, exist_ok=False)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("\n===== 관계 확장 결과 =====")
    print(f"검증 통과: {output['validation_passed']}")
    print(f"후보 수: {len(result.get('expansions', []))}")
    print(f"사용 토큰: {usage['total_tokens']}")
    print(f"저장: {output_path.relative_to(ROOT)}")

    if validation_errors:
        print("\n검증 오류:")
        for error in validation_errors:
            print(f"- {error}")

    for item in result.get("expansions", []):
        print(
            f"- {item.get('target_topic')} "
            f"({item.get('relation_type')})"
        )


if __name__ == "__main__":
    main()