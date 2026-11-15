"""Wikidata 관계 보강 설정과 상수."""
import os
import re
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

CACHE_DIR = ROOT / "data" / "enrichment" / "wikidata"



WIKIDATA_API = "https://www.wikidata.org/w/api.php"

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"



REQUEST_DELAY_SECONDS = 1.2

BATCH_SIZE = 40

SPARQL_BATCH_SIZE = 20



load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]



WIKIMEDIA_CONTACT = os.getenv(

          "WIKIMEDIA_CONTACT",

          "mailto:content-exploration@example.invalid",

)

USER_AGENT = (

          f"content-exploration-capstone/1.0 ({WIKIMEDIA_CONTACT})"

)



SOURCE_NAME = "wikidata"



# 이 단계에서 생성하는 관계는 검증 후보로 저장한다.

FACT_STATUS = "proposed"

VISIBILITY = "shown"



TYPE_SCORE_VERSION = "v3-wikidata-p31"





# =========================================================

# 2. Wikidata 타입 분류 기준

#          06_resolve_candidate_wikidata_types.py와 동일

# =========================================================



HUMAN_QID = "Q5"



WORK_INSTANCE_QIDS = {

          "Q11424",

          "Q5398426",

          "Q571",

          "Q7725634",

          "Q47461344",

          "Q11060274",

          "Q7889",

}



EVENT_INSTANCE_QIDS = {

          "Q1190554",

          "Q1656682",

          "Q178561",

          "Q198",

          "Q40231",

}



ORGANIZATION_INSTANCE_QIDS = {

          "Q43229",

          "Q4830453",

          "Q2088357",

}



PLACE_INSTANCE_QIDS = {

          "Q17334923",

          "Q2221906",

          "Q515",

          "Q6256",

          "Q82794",

}



NEXT_HOP_CATEGORIES = {"event", "person", "work"}





# =========================================================

# 3. Wikidata 관계 추출 규칙

# =========================================================



CLAIM_RULES = {

          # 창작자·감독·각본가·저자
          "P57": ("created_by", "person"),

          "P58": ("created_by", "person"),

          "P170": ("created_by", "person"),

          "P50": ("created_by", "person"),



          # 출연자
          "P161": ("acted_by", "person"),



          # 원작·각색 관계
          "P144": ("adapted_from", "work"),



          # 주제·묘사 대상
          "P180": ("about_concept", None),

          "P921": ("about_concept", None),

}



CONTENT_CLAIMS = {

          "movie": (

                    "P57", "P58", "P170", "P161",

                    "P144", "P180", "P921",

          ),

          "tv": (

                    "P57", "P58", "P170", "P161",

                    "P144", "P180", "P921",

          ),

          "book": (

                    "P50", "P144", "P180", "P921",

          ),

}





# =========================================================

# 4. 공통 API 함수

# =========================================================
