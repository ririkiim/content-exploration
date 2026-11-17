import json
import os
import re
import unicodedata
from collections import Counter

from dotenv import load_dotenv
from openai import OpenAI

from topic_prompt import (
    SYSTEM_PROMPT,
    build_prompt,
    prepare_content,
)


load_dotenv()

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5.6")

SOMSOM_BASE_URL = os.getenv(
    "SOMSOM_BASE_URL",
    "https://factchat-cloud.mindlogic.ai/v1/gateway",
)

VALID_TOPIC_TYPES = {
    "subject",
    "theme",
    "social_issue",
    "human_experience",
}

VALID_EVIDENCE_LEVELS = {
    "explicit",
    "inferred",
}

VALID_CONFIDENCE_LEVELS = {
    "high",
    "medium",
}


def get_client():
    api_key = os.environ["OPENAI_API_KEY"]

    return OpenAI(
        api_key=api_key,
        timeout=120.0,
        max_retries=2,
    )


# =========================================================
# 1. LLM 호출
# =========================================================

def call_llm(
    content,
    model_name=MODEL_NAME,
    temperature=0.0,
    json_mode=False,
):
    request = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": build_prompt(content),
            },
        ],
        "stream": False,
    }

    if temperature is not None:
        request["temperature"] = temperature

    if json_mode:
        request["response_format"] = {
            "type": "json_object"
        }

    if model_name.startswith("gpt-5.6"):
        request.pop("temperature", None)

    response = get_client().chat.completions.create(
        **request
    )

    raw_response = response.choices[0].message.content

    if not raw_response:
        raise ValueError("LLM 응답이 비어 있습니다.")

    return raw_response


# =========================================================
# 2. JSON 파싱
# =========================================================

def normalize_topic_name(name):
    return " ".join(name.split())


def parse_response(
    raw_response,
    expected_content_id,
):
    text = raw_response.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"\s*```$",
            "",
            text,
        )

    data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError(
            "LLM 응답이 JSON 객체가 아닙니다."
        )

    if str(data.get("content_id")) != str(
        expected_content_id
    ):
        raise ValueError(
            "응답 content_id가 입력과 일치하지 않습니다."
        )

    raw_topics = data.get("topics")

    if not isinstance(raw_topics, list):
        raise ValueError(
            "topics 필드가 배열이 아닙니다."
        )

    required_fields = {
        "name",
        "type",
        "evidence_quote",
        "evidence",
        "evidence_level",
        "confidence",
    }

    parsed_topics = []

    for index, topic in enumerate(raw_topics):
        if not isinstance(topic, dict):
            raise ValueError(
                f"topics[{index}]가 객체가 아닙니다."
            )

        missing = required_fields - topic.keys()

        if missing:
            raise ValueError(
                f"topics[{index}] 필드 누락: "
                f"{sorted(missing)}"
            )

        for field in required_fields:
            if not isinstance(topic[field], str):
                raise ValueError(
                    f"topics[{index}].{field}는 "
                    "문자열이어야 합니다."
                )

        name = normalize_topic_name(
            topic["name"]
        )

        if not name:
            raise ValueError(
                f"topics[{index}] 이름이 비어 있습니다."
            )

        topic_type = topic["type"].strip()
        evidence_level = topic["evidence_level"].strip()
        confidence = topic["confidence"].strip()

        if topic_type not in VALID_TOPIC_TYPES:
            raise ValueError(
                f"허용되지 않은 topic_type: {topic_type}"
            )

        if evidence_level not in VALID_EVIDENCE_LEVELS:
            raise ValueError(
                "허용되지 않은 evidence_level: "
                f"{evidence_level}"
            )

        # low도 평가 이력에는 남긴다.
        if confidence not in (
            VALID_CONFIDENCE_LEVELS | {"low"}
        ):
            raise ValueError(
                f"허용되지 않은 confidence: {confidence}"
            )

        parsed_topics.append({
            "name": name,
            "type": topic_type,
            "evidence_quote": topic[
                "evidence_quote"
            ].strip(),
            "evidence": topic["evidence"].strip(),
            "evidence_level": evidence_level,
            "confidence": confidence,
            "review_flags": [],
        })

    return parsed_topics


# =========================================================
# 3. 문자열 정규화
# =========================================================

def normalize_for_match(value):
    text = unicodedata.normalize(
        "NFC",
        str(value or ""),
    )

    return " ".join(
        text.split()
    ).casefold()


def normalize_with_positions(text):
    """
    정규화한 문자열과 원문 위치 매핑을 생성한다.

    반환:
        normalized_text
        positions: [(start, end), ...]
    """

    normalized_chars = []
    positions = []

    text = str(text or "")

    # 결합형 한글 등 NFC 조합을 먼저 처리한다.
    # 위치는 원문 기준으로 다시 매핑한다.
    nfc_text = unicodedata.normalize(
        "NFC",
        text,
    )

    previous_space = False

    for index, char in enumerate(nfc_text):
        if char.isspace():
            if (
                not normalized_chars
                or previous_space
            ):
                continue

            normalized_chars.append(" ")
            positions.append((index, index + 1))
            previous_space = True

        else:
            folded = char.casefold()

            for folded_char in folded:
                normalized_chars.append(
                    folded_char
                )

                positions.append(
                    (index, index + 1)
                )

            previous_space = False

    if (
        normalized_chars
        and normalized_chars[-1] == " "
    ):
        normalized_chars.pop()
        positions.pop()

    return "".join(normalized_chars), positions


# =========================================================
# 4. 인용 출처
# =========================================================

def get_sources(content):
    prepared = prepare_content(content)

    sources = []

    description = prepared.get(
        "description"
    )

    if description:
        sources.append(
            ("description", str(description))
        )

    keywords = prepared.get(
        "keywords"
    ) or []

    for index, keyword in enumerate(keywords):
        sources.append(
            (
                f"keywords[{index}]",
                str(keyword),
            )
        )

    return sources


def find_quote(quote, sources):
    normalized_quote = normalize_for_match(
        quote
    )

    if not normalized_quote:
        return None

    for source_name, source_text in sources:
        normalized_text, positions = (
            normalize_with_positions(source_text)
        )

        start = normalized_text.find(
            normalized_quote
        )

        if start == -1:
            continue

        end = start + len(normalized_quote)

        return {
            "evidence_source": source_name,
            "evidence_location": {
                "start": positions[start][0],
                "end": positions[end - 1][1],
            },
        }

    return None


# =========================================================
# 5. 인용 검증
# =========================================================

def validate_evidence_quote(
    topic,
    sources,
):
    quote = topic["evidence_quote"]

    location = find_quote(
        quote,
        sources,
    )

    quote_valid = location is not None

    fragments = [
        fragment.strip()
        for fragment in re.split(
            r"(?:\.{3,}|…+)",
            quote,
        )
        if fragment.strip()
    ]

    fragments_valid = (
        bool(fragments)
        and all(
            find_quote(
                fragment,
                sources,
            ) is not None
            for fragment in fragments
        )
    )

    flags = list(
        topic.get("review_flags", [])
    )

    if not quote_valid:
        flags.append(
            "quote_not_found"
        )

    if re.search(
        r"(?:\.{3,}|…+)",
        quote,
    ):
        flags.append(
            "ellipsis_review"
        )

    if (
        not quote_valid
        and fragments_valid
    ):
        flags.append(
            "fragments_found_but_not_contiguous"
        )

    if len(topic["name"]) > 15:
        flags.append(
            "long_topic_name"
        )

    if re.search(
        r"\s(?:와|과)\s",
        topic["name"],
    ):
        flags.append(
            "combined_topic_name_review"
        )

    # 짧은 인용은 자동 탈락시키지 않는다.
    # 검토 대상으로만 표시한다.
    if quote_valid and len(
        normalize_for_match(quote)
    ) < 5:

        source_name = location[
            "evidence_source"
        ]

        keyword_exact = (
            source_name.startswith("keywords[")
            and any(
                name == source_name
                and normalize_for_match(value)
                == normalize_for_match(quote)
                for name, value in sources
            )
        )

        if not keyword_exact:
            flags.append(
                "short_quote_review"
            )

    return {
        "quote_valid": quote_valid,
        "quote_fragments_valid": fragments_valid,
        "evidence_source": (
            location["evidence_source"]
            if location
            else None
        ),
        "evidence_location": (
            location["evidence_location"]
            if location
            else None
        ),
        "review_flags": flags,
    }


# =========================================================
# 6. 전체 토픽 검증
# =========================================================

def validate_topics(
    topics,
    content,
):
    # 콘텐츠당 출처 한 번만 준비
    sources = get_sources(content)

    quote_counts = Counter(
        normalize_for_match(
            topic["evidence_quote"]
        )
        for topic in topics
    )

    name_counts = Counter(
        normalize_for_match(
            topic["name"]
        )
        for topic in topics
    )

    seen_names = set()

    for topic in topics:
        topic.update(
            validate_evidence_quote(
                topic,
                sources,
            )
        )

        quote_key = normalize_for_match(
            topic["evidence_quote"]
        )

        name_key = normalize_for_match(
            topic["name"]
        )

        if quote_counts[quote_key] > 1:
            topic["review_flags"].append(
                "duplicate_quote_review"
            )

        if name_counts[name_key] > 1:
            topic["review_flags"].append(
                "duplicate_topic_name"
            )

        if name_key in seen_names:
            topic["review_flags"].append(
                "dropped_duplicate"
            )

        seen_names.add(name_key)

        if topic["confidence"] == "low":
            topic["review_flags"].append(
                "dropped_low"
            )

    return topics