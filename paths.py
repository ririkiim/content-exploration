# 프로젝트 내 데이터 저장 위치를 한 곳에서 관리하기 위한 경로 설정

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TMDB_RAW_DIR = DATA_DIR / "raw" / "tmdb"
KMDB_RAW_DIR = DATA_DIR / "raw" / "kmdb"