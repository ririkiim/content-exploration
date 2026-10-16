import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

CACHE_DIR = ROOT / "data" / "enrichment" / "candidate_wikidata"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

WIKIPEDIA_API = "https://ko.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

REQUEST_DELAY_SECONDS = 1.2
BATCH_SIZE = 40
TYPE_SCORE_VERSION = "v5-wikidata-p31-p279"

REFINED_RELATION_TYPES = [
    "wikipedia_context_candidate",
    "wikipedia_original_candidate",
    "wikipedia_series_candidate",
    "wikipedia_related_work_candidate",
    "wikipedia_creator_candidate",
    "wikipedia_mention_candidate",
]

HUMAN_QID = "Q5"

WORK_INSTANCE_QIDS = {
    "Q11424",       # film
    "Q5398426",     # television series
    "Q571",         # book
    "Q7725634",     # literary work
    "Q47461344",    # written work
    "Q11060274",    # webtoon
    "Q7889",        # video game
}

EVENT_INSTANCE_QIDS = {
    "Q1190554",     # occurrence
    "Q1656682",     # event
    "Q178561",      # battle
    "Q198",         # war
    "Q40231",       # election
}

ORGANIZATION_INSTANCE_QIDS = {
    "Q43229",       # organization
    "Q4830453",     # business
    "Q2088357",     # publisher
}

PLACE_INSTANCE_QIDS = {
    "Q17334923",    # location
    "Q2221906",     # geographic location
    "Q515",         # city
    "Q6256",        # country
    "Q82794",       # geographic region
}

AWARD_INSTANCE_QIDS = {
    "Q618779",      # award
}

GENRE_INSTANCE_QIDS = {
    "Q188451",      # genre
    "Q483394",      # literary genre
}

NEXT_HOP_CATEGORIES = {
    "person",
    "work",
    "event",
}