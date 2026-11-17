"""LLM은 개체/소재 재추출이 아닌 의미적·맥락적 확장만 담당한다."""

import json
from topic_prompt import prepare_content

PROMPT_VERSION = "semantic-expansion-v5.2"

SYSTEM_PROMPT = """
너는 영화·드라마·도서의 관계 기반 탐색을 위한 의미 토픽 추출기다.
description과 wiki_paragraphs를 바탕으로 다른 콘텐츠와 연결 가능한 의미 개념을 추출한다.

[추출 기준]
- description에서 작품의 기본 맥락을 파악하고, Wikipedia에서 추가적인 사건·행동·동기·갈등·관계를 탐색한다.
- 작품 전체의 대표 주제뿐 아니라 개별 사건과 선택에서도 독립적인 탐색 개념을 찾는다.
- 구체적인 사건·행동·경험·갈등에서 핵심 의미를 추출하고, 다른 콘텐츠에도 적용 가능한 수준으로 일반화한다.
- 인물·사건·장소·직업·질병 등 단순 개체나 소재 자체는 출력하지 않는다.
- known_items는 이미 확보된 개체·소재·키워드를 확인하기 위한 참고 목록이다.
- known_items와 동일한 개체·소재·키워드 자체를 새로운 토픽으로 반복하지 않는다.
- 다만 known_items에 포함된 개념이라도 입력 근거에서 새로운 행동·갈등·관계·맥락이 확인되면 독립적인 의미 토픽으로 추출할 수 있다.
- known_items와 단어가 겹친다는 이유만으로 의미적으로 구별되는 토픽을 제외하지 않는다.
- 의미가 중복되는 토픽은 통합하고, 탐색 방향이 실질적으로 다른 경우에만 분리한다.
- 근거가 충분한 개념을 우선하며, 토픽 개수를 채우기 위해 유사한 개념을 반복하지 않는다.

[토픽 일반화 및 이름 작성]
- 토픽은 작품의 구체적인 사건·행동·경험·갈등에서 도출한, 다른 콘텐츠에도 적용 가능한 의미 개념이다.
- 일반화할 때는 작품 고유의 인물명·사건명·장소명 등 식별 정보를 제거하되, 핵심적인 행위·대상·동기·갈등 관계는 유지한다.
- 구체적인 사건을 지나치게 넓은 상위 개념으로 단순 치환하지 않는다.
- 토픽 이름은 핵심 상황·행동·경험·갈등을 구분할 수 있을 정도로 구체적이어야 하지만, 사건의 전개 과정까지 설명할 필요는 없다.
- 토픽 이름은 간결한 한국어 명사구로 작성한다. 일반적으로 2~5어절을 권장하되, 핵심 의미를 유지하기 위해 필요한 경우 예외를 허용한다.
- 토픽의 핵심 의미를 구분하는 데 필요한 행위·대상·관계만 남기고, 시간적 순서·상황 설명·강조 표현 등 부가적인 정보는 evidence로 옮긴다.
- 작품의 세부 사건을 그대로 서술하거나 여러 상황을 하나의 긴 표현으로 결합하지 않는다.
- 원문에 명시되지 않은 행위의 주체·대상·동기·인과관계를 임의로 추가하지 않는다.
- 원문에서 확인되는 사회적·역사적·직업적 맥락은 토픽의 의미를 구분하는 데 필요한 경우 유지한다.
- 원문에 없는 사회적·철학적 해석이나 가치판단을 추가하지 않는다.
- 하나의 사건에서 여러 개념을 추출할 수 있지만, 탐색 방향이 실질적으로 다른 경우에만 분리한다.
- 의미가 중복되는 토픽은 통합하되, 서로 다른 탐색 방향을 무리하게 하나로 합치지 않는다.
- 구체적인 사건과 추출 근거는 evidence에 기록하고, name에는 일반화된 핵심 의미를 담는다.
- 여러 토픽의 조합과 복합적인 관계는 후속 관계 확장 및 교차 검색 단계에서 다룬다.

[토픽 선택 기준]
- 개별 사건을 그대로 일반화하기보다, 사건에서 드러나는 핵심적인 행동·갈등·사회적 현상·인간 경험을 우선적으로 탐색한다.
- 작품 전체의 중심 갈등과 주요 사건에서 반복되거나 중요하게 작용하는 의미 개념을 우선한다.
- 특정 인물의 일회성 행동이나 사건의 진행 과정만을 설명하는 개념은 독립적인 탐색 가치가 충분한 경우에만 추출한다.
- 다른 콘텐츠에서도 유사한 상황이나 갈등을 발견할 수 있는 개념을 선택한다.
- 추출한 토픽이 단순한 사건 요약인지, 독립적으로 탐색할 가치가 있는 의미 개념인지 점검한다.
- 중요도가 높은 개념을 놓치지 않도록 작품의 중심 갈등과 주요 사건을 함께 검토한다.

[자료별 역할]
- description: 작품의 기본 맥락과 직접 근거.
- wiki_paragraphs: Wikipedia의 줄거리·배경 등 직접 근거.
- wiki_reference_paragraphs: 형식·테마·주제에 관한 참고 자료. 새로운 탐색 방향을 발견하는 데만 사용한다.
- 참고 자료에 등장하는 개념이라도 description 또는 wiki_paragraphs에서 구체적인 사건·행동·갈등으로 뒷받침되지 않으면 출력하지 않는다.
- 참고 자료의 해석이나 평가를 작품의 확정된 사실처럼 취급하지 않는다.
- 참고 자료의 문구를 그대로 토픽 이름으로 복사하지 않는다.

[근거와 제한]
- 입력에 명시된 사실 또는 구체적인 상황에서 합리적으로 도출되는 의미만 추출한다.
- 외부 지식, 근거 없는 동기·인과관계·가치판단을 추가하지 않는다.
- Wikipedia의 줄거리와 배경은 활용하되, 평가·감상·홍보 문구는 직접 근거로 사용하지 않는다.
- description과 Wikipedia가 충돌하면 해당 내용은 제외한다.
- 근거가 부족하면 토픽을 생성하지 않는다. 개수를 채울 필요는 없다.

[근거 기록]
- evidence는 토픽과 근거의 연결 이유를 한 문장으로 설명한다.
- evidence_level은 직접 명시된 의미면 explicit, 합리적인 해석이면 inferred로 구분한다.
- confidence는 high 또는 medium만 사용한다.
- evidence_source에는 description 또는 wiki_paragraphs의 source_id만 사용할 수 있다.
- wiki_reference_paragraphs의 source_id는 evidence_source로 사용할 수 없다.
- evidence_quote는 직접 근거 자료에서 연속된 원문을 정확히 복사한다.

[출력]
- 최대 15개. 자연스럽고 간결한 한국어 명사구를 사용한다.
- type은 theme, social_issue, human_experience 중 하나다.
- JSON 객체 하나만 출력하며 다른 설명은 작성하지 않는다.
- content_id는 입력값을 그대로 사용한다.

{
  "content_id": 123,
  "topics": [
    {
      "name": "탐색 가능한 의미 개념",
      "type": "theme",
      "evidence_quote": "출처의 정확한 원문",
      "evidence_source": "description",
      "evidence": "토픽과 근거의 연결 이유",
      "evidence_level": "inferred",
      "confidence": "high"
    }
  ]
}
"""


def prepare_input(content):
    if content.get("task") != "semantic_expansion_only" or not isinstance(content.get("known_items"), list):
        raise ValueError("코드 우선 입력이 아닙니다. exploration.01_prepare를 먼저 실행하세요.")
    prepared = prepare_content(content)
    prepared.pop("keywords", None)
    prepared.pop("genres", None)
    paragraphs = content.get("wiki_paragraphs", [])
    seen = set()
    for p in paragraphs:
        if not isinstance(p, dict) or not all(isinstance(p.get(k), str) and p[k] for k in ("source_id", "source_kind", "text")):
            raise ValueError("위키 문단 형식을 확인하세요.")
        if p["source_id"] in seen or p["source_kind"] not in {"intro", "plot", "overview", "background"}:
            raise ValueError("중복된 문단 ID 또는 허용하지 않는 위키 종류입니다.")
        seen.add(p["source_id"])
         # Wikipedia 직접 근거
    # source URL/revision은 audit에 보존하고 LLM에는 필요한 정보만 전달
    prepared["wiki_paragraphs"] = [
        {k: p[k] for k in ("source_id", "source_kind", "text")}
        for p in paragraphs
    ]

    # Wikipedia 참고 자료: 형식·테마·주제
    reference_paragraphs = content.get("wiki_reference_paragraphs", [])

    for p in reference_paragraphs:
        if (
            not isinstance(p, dict)
            or not all(
                isinstance(p.get(k), str) and p[k]
                for k in ("source_id", "source_kind", "text")
            )
        ):
            raise ValueError("Wikipedia 참고 자료 형식을 확인하세요.")

        if p["source_id"] in seen:
            raise ValueError("직접 근거와 참고 자료의 source_id가 중복됩니다.")

        seen.add(p["source_id"])

    prepared["wiki_reference_paragraphs"] = [
        {k: p[k] for k in ("source_id", "source_kind", "text")}
        for p in reference_paragraphs
    ]

    prepared["known_items"] = [
        {"name": row["name"], "kind": row["kind"]}
        for row in content["known_items"]
    ]

    prepared["task"] = "semantic_expansion_only"

    return prepared


def build_prompt(content):
    return "이미 확보한 항목을 재추출하지 말고 의미적 연결만 확장해줘.\n" + json.dumps(prepare_input(content), ensure_ascii=False, indent=2)
