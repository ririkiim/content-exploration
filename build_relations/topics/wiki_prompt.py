"""기존 실행기의 호환 진입점. 실제 기준은 semantic_prompt.py에 있다."""
from semantic_prompt import (
    SYSTEM_PROMPT as WIKI_SYSTEM_PROMPT,
    PROMPT_VERSION,
    prepare_input as prepare_wiki_input,
    build_prompt as build_wiki_prompt,
)
