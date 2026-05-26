import json
import urllib.request
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler

CACHE_FILE = "/tmp/whale_cache.json"
CACHE_HOURS = 1

POLYMARKET_URL = "https://gamma-api.polymarket.com/markets?active=true&limit=100&order=volume24hr&ascending=false"

def http_get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data if isinstance(data, list) else data.get("markets", [])

def parse_yes_prob(m):
    try:
        return float(json.loads(m.get("outcomePrices", "[null]"))[0])
    except Exception:
        return None

def severity(flags):
    n = len(flags)
    if n >= 2: return "HIGH"
    if n == 1: return "MEDIUM"
    return "LOW"

def detect_whales(markets):
    flagged = []
    for m in markets:
        yp = parse_yes_prob(m)
        if yp is None:
            continue
        try:
            wc = float(m.get("oneWeekPriceChange") or 0)
        except Exception:
            wc = 0
        try:
            liq = float(m.get("liquidityNum") or 0)
        except Exception:
            liq = 0
        try:
            vol = float(m.get("volumeNum") or 0)
        except Exception:
            vol = 0
        try:
            vol24 = float(m.get("volume24hr") or 0)
        except Exception:
            vol24 = 0

        flags = []

        if abs(wc) >= 0.20:
            flags.append({
                "type": "velocity_spike",
                "label": "Velocity Spike",
                "detail": f"Odds moved {round(wc*100)}% in 7 days",
                "direction": "up" if wc > 0 else "down",
                "explanation": "Odds shifted dramatically in a short window with no corresponding news event. Sudden large moves are a classic signature of coordinated buying by a whale pushing a market toward a predetermined outcome."
            })

        if liq < 5000 and vol > 50000:
            flags.append({
                "type": "thin_liquidity",
                "label": "Thin Liquidity",
                "detail": f"${int(vol):,} volume on only ${int(liq):,} liquidity",
                "explanation": "A large volume of money has moved through a market with very little liquidity to support it. When someone trades heavily on a shallow market, they can move the odds significantly with relatively little capital — a common manipulation tactic."
            })

        if yp > 0.88 or yp < 0.12:
            flags.append({
                "type": "certainty_creep",
                "label": "Certainty Creep",
                "detail": f"Odds at {round(yp*100)}% — approaching forced resolution territory",
                "explanation": "This market's odds have drifted above 88% or below 12% while still active and unresolved. Markets at these extremes are vulnerable to governance attacks where token-weighted resolution votes can be manipulated by whales who accumulated positions at favourable prices."
            })

        if liq > 0 and vol24 > 0 and (vol24 / liq) > 3:
            flags.append({
                "type": "volume_surge",
                "label": "Volume Surge",
                "detail": f"24hr volume is {round(vol24/liq, 1)}x the available liquidity",
                "explanation": "24-hour trading volume is more than 3x the available liquidity. This level of turnover relative to pool size is statistically abnormal and often precedes sharp price reversals or resolution disputes."
            })

        if flags:
            flagged.append({
                "question": m.get("question"),
                "slug": m.get("slug"),
                "yes_prob": round(yp, 4),
                "liquidity": liq,
                "volume": vol,
                "volume_24hr": vol24,
                "week_change": round(wc, 4),
                "flags": flags,
                "flag_count": len(flags),
                "severity": severity(flags),
                "polymarket_url": f"https://polymarket.com/event/{m.get('slug', '')}"
            })

    flagged.sort(key=lambda x: (x["flag_count"], abs(x["week_change"])), reverse=True)
    return flagged[:20]

def run_whale_scan():
    try:
        with open(CACHE_FILE, "r") as f:
            cached = json.load(f)
        cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
        if datetime.now(timezone.utc) - cached_at.replace(tzinfo=timezone.utc) < timedelta(hours=CACHE_HOURS):
            print("WHALE: returning cached result", file=sys.stderr)
            return cached.get("data", [])
    except Exception:
        pass

    print("WHALE: fetching fresh data", file=sys.stderr)
    markets = http_get(POLYMARKET_URL)
    flagged = detect_whales(markets)

    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"cached_at": datetime.now(timezone.utc).isoformat(), "data": flagged}, f)
    except Exception:
        pass

    return flagged


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            result = run_whale_scan()
            self._respond(200, {
                "flagged_markets": result,
                "count": len(result),
                "scanned_at": datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            tb = traceback.format_exc()
            print("WHALE ERROR:", e, file=sys.stderr)
            print(tb, file=sys.stderr)
            self._respond(500, {"error": str(e), "trace": tb})

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
