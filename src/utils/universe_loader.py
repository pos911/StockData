import json
import os

def load_universe():
    """config/stock_universe.json 파일을 읽어서 유니버스를 반환합니다."""
    path = "config/stock_universe.json"
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
