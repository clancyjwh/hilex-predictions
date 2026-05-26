import json
import urllib.request
import urllib.parse
import traceback
import sys
from http.server import BaseHTTPRequestHandler

def http_get(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_polymarket(query):
    encoded_query = urllib.parse.quote(query)
    url = f"https://gamma-api.polymarket.com/markets?active=true&limit=20&keyword={encoded_query}"
    data = http_get(url)
    
    # Endpoint /markets can return a list or dict with markets key
    markets = data if isinstance(data, list) else data.get("markets", [])
    
    slim = []
    for m in markets:
        try:
            prices = m.get("outcomePrices")
            yes_prob = None
            if isinstance(prices, list) and len(prices) > 0:
                yes_prob = float(prices[0]) if prices[0] is not None else None
            elif isinstance(prices, str):
                p_list = json.loads(prices)
                if isinstance(p_list, list) and len(p_list) > 0:
                    yes_prob = float(p_list[0]) if p_list[0] is not None else None
            
            slim.append({
                "slug": m.get("slug"),
                "question": m.get("question"),
                "yes_prob": yes_prob,
                "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
                "volume": float(m.get("volumeNum") or m.get("volume") or 0),
                "week_change": float(m.get("oneWeekPriceChange") or 0)
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
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            body = json.loads(post_data.decode('utf-8'))
            query = (body.get("query") or "").strip()
            
            if not query:
                self._respond(400, {"error": "query is required"})
                return
            
            result = fetch_polymarket(query)
            self._respond(200, result)
        except Exception as e:
            tb = traceback.format_exc()
            self._respond_err(500, str(e), tb)

    def _respond(self, status, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def _respond_err(self, status, msg, tb=""):
        print("HILEX ERROR:", msg, file=sys.stderr)
        print(tb, file=sys.stderr)
        payload = json.dumps({"error": msg, "trace": tb}).encode("utf-8")
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
