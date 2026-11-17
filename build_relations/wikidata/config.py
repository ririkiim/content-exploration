import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

API_URL = "https://www.wikidata.org/w/api.php"
SPARQL_URL = "https://query.wikidata.org/sparql"

REQUEST_DELAY = 1.2

USER_AGENT = (
    "content-exploration/1.0 "
    f"({os.getenv('WIKIMEDIA_CONTACT', 'local research tool')})"
)