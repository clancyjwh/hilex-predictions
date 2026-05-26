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

def fetch_polymarket(keyword):
    params = urllib.parse.urlencode({"active": "true", "limit": 20, "keyword": keyword})
    data = http_get(f"{POLYMARKET_API}?{params}")
    markets = data if isinstance(data, list) else data.get("markets", [])
    slim = []
    for m in markets:
        try:
            prices = json.loads(m.get("outcomePrices", "[null]"))
            slim.append({
                "slug": m.get("slug"),
                "question": m.get("question"),
                "yes_prob": float(prices[0]) if prices[0] else None,
                "liquidity": m.get("liquidityNum", 0),
                "volume": m.get("volumeNum", 0),
                "week_change": m.get("oneWeekPriceChange", 0)
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
