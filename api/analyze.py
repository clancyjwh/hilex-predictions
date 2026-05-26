import json
import urllib.request
import urllib.parse
import traceback
from http.server import BaseHTTPRequestHandler

POLYMARKET_API = "https://gamma-api.polymarket.com/markets"

def http_get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def find_outcome_index(outcomes, query):
    if not outcomes:
        return 0
    query_lower = query.lower()
    
    # 1. Check if it's a binary Yes/No market
    normalized_outcomes = [str(o).lower().strip() for o in outcomes]
    if len(normalized_outcomes) == 2 and ("yes" in normalized_outcomes or "no" in normalized_outcomes):
        if "yes" in normalized_outcomes:
            return normalized_outcomes.index("yes")
        return 0

    # 2. For categorical markets, find the best matching outcome based on query
    import re
    query_words = [w for w in re.findall(r'\b\w+\b', query_lower) if len(w) > 2]
    
    best_idx = 0
    best_score = -1
    
    for idx, outcome in enumerate(normalized_outcomes):
        score = 0
        if outcome in query_lower or query_lower in outcome:
            score += 10
            
        outcome_words = [w for w in re.findall(r'\b\w+\b', outcome) if len(w) > 2]
        overlap = set(query_words).intersection(set(outcome_words))
        score += len(overlap) * 2
        
        # Mention position bonus (prefer outcomes mentioned earlier in query)
        first_mention_pos = 9999
        for w in outcome_words:
            pos = query_lower.find(w)
            if pos != -1 and pos < first_mention_pos:
                first_mention_pos = pos
        
        if first_mention_pos < 9999:
            score += (1000 - first_mention_pos)
            
        if score > best_score:
            best_score = score
            best_idx = idx
            
    return best_idx

def fetch_polymarket(keyword):
    params = urllib.parse.urlencode({"active": "true", "limit": 20, "keyword": keyword})
    data = http_get(f"{POLYMARKET_API}?{params}")
    markets = data if isinstance(data, list) else data.get("markets", [])
    slim = []
    for m in markets:
        try:
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except Exception:
                    prices = [None]
            if not isinstance(prices, list):
                prices = [None]
            
            outcomes = m.get("outcomes")
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    outcomes = []
            if not isinstance(outcomes, list):
                outcomes = []

            idx = find_outcome_index(outcomes, keyword)
            
            yes_prob = None
            if len(prices) > idx and prices[idx] is not None and prices[idx] != "":
                try:
                    yes_prob = float(prices[idx])
                except Exception:
                    pass

            try:
                liquidity = float(m.get("liquidityNum") or 0)
            except Exception:
                liquidity = 0.0

            try:
                volume = float(m.get("volumeNum") or 0)
            except Exception:
                volume = 0.0

            try:
                week_change = float(m.get("oneWeekPriceChange") or 0)
            except Exception:
                week_change = 0.0

            slim.append({
                "slug": m.get("slug"),
                "question": m.get("question"),
                "yes_prob": yes_prob,
                "liquidity": liquidity,
                "volume": volume,
                "week_change": week_change
            })
        except Exception:
            continue
    return slim

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            query = (body.get("query") or "").strip()
            if not query:
                self._respond(400, {"error": "query is required"})
                return
            markets = fetch_polymarket(query)
            self._respond(200, {"markets": markets, "count": len(markets)})
        except Exception as e:
            self._respond(500, {"error": str(e), "trace": traceback.format_exc()})

    def _respond(self, status, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *args):
        pass
