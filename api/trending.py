import json
import urllib.request
import os
import concurrent.futures
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

CACHE_FILE = '/tmp/trending_cache.json'
if os.name == 'nt':
    import tempfile
    CACHE_FILE = os.path.join(tempfile.gettempdir(), 'trending_cache.json')

POLYMARKET_TRENDING = "https://gamma-api.polymarket.com/markets?active=true&limit=100&order=volume24hr&ascending=false"
POLYMARKET_MOVERS   = "https://gamma-api.polymarket.com/markets?active=true&limit=100"

def http_get(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data if isinstance(data, list) else data.get("markets", [])

def http_post(url, payload, headers):
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def safe_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def parse_yes_prob(m):
    try:
        prices = m.get("outcomePrices")
        if isinstance(prices, list):
            return float(prices[0]) if len(prices) > 0 and prices[0] is not None else None
        if isinstance(prices, str):
            p_list = json.loads(prices)
            return float(p_list[0]) if isinstance(p_list, list) and len(p_list) > 0 and p_list[0] is not None else None
        return None
    except Exception:
        return None

def get_trending(markets):
    now = datetime.now(timezone.utc)
    filtered = []
    for m in markets:
        yp = parse_yes_prob(m)
        if yp is None or yp > 0.95 or yp < 0.05:
            continue
        try:
            end = m.get("endDate", "")
            if end and datetime.fromisoformat(end.replace("Z", "+00:00")) < now:
                continue
        except Exception:
            pass
        filtered.append(m)

    seen, deduped = [], []
    for m in filtered:
        words = "-".join(m.get("slug", "").split("-")[:4])
        if not any(len([w for w in words.split("-") if w in s.split("-")]) >= 3 for s in seen):
            seen.append(words)
            yp = parse_yes_prob(m)
            deduped.append({"question": m.get("question"), "slug": m.get("slug"),
                            "yes_prob": str(yp), "liquidity": m.get("liquidityNum", 0),
                            "volume": m.get("volumeNum", 0),
                            "week_change": m.get("oneWeekPriceChange", 0), "tag": "trending"})
        if len(deduped) == 10:
            break
    return deduped

def get_big_movers(markets):
    movers = []
    for m in markets:
        try:
            wc = float(m.get("oneWeekPriceChange") or 0)
        except Exception:
            wc = 0
        if abs(wc) >= 0.15:
            yp = parse_yes_prob(m)
            movers.append({"question": m.get("question"), "slug": m.get("slug"),
                           "yes_prob": str(yp), "liquidity": m.get("liquidityNum", 0),
                           "volume": m.get("volumeNum", 0), "week_change": wc,
                           "tag": "big_mover", "direction": "up" if wc > 0 else "down"})
    movers.sort(key=lambda x: abs(float(x["week_change"])), reverse=True)
    return movers[:5]

def normalize_questions(questions, big_movers):
    payload = json.dumps({"questions": questions, "big_movers": big_movers})
    resp = http_post(
        OPENAI_API_URL,
        {"model": "gpt-4o", "messages": [
            {"role": "system", "content": "You are a prediction market question normalizer. Ensure every question is a clean binary YES/NO. If already binary leave unchanged. If ambiguous rewrite as 'Will X happen?'. Never change any field except question. Return ONLY the same JSON structure as input, no markdown."},
            {"role": "user", "content": payload}
        ], "temperature": 0.1},
        {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}
    )
    return safe_json(resp["choices"][0]["message"]["content"])

def run_trending():
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_t = ex.submit(http_get, POLYMARKET_TRENDING)
        f_m = ex.submit(http_get, POLYMARKET_MOVERS)
        trending_raw = f_t.result()
        movers_raw   = f_m.result()
    questions  = get_trending(trending_raw)
    big_movers = get_big_movers(movers_raw)
    return normalize_questions(questions, big_movers)


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            use_cache = False
            result = None
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, 'r') as f:
                        cache = json.load(f)
                    ts = cache.get("timestamp", 0)
                    if time.time() - ts < 86400:
                        result = cache.get("data")
                        if result:
                            use_cache = True
                except Exception:
                    pass

            if not use_cache:
                result = run_trending()
                try:
                    with open(CACHE_FILE, 'w') as f:
                        json.dump({"timestamp": time.time(), "data": result}, f)
                except Exception:
                    pass

            self._respond(200, result)
        except Exception as e:
            self._respond(500, {"error": str(e)})

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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *args):
        pass
