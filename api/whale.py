import json
import urllib.request
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor

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
        end = m.get("endDate", "")
        if end:
            try:
                if datetime.fromisoformat(end.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                    continue
            except Exception:
                pass

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

        is_near_expiry = False
        if end:
            try:
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                if (end_dt - datetime.now(timezone.utc)).days <= 7:
                    is_near_expiry = True
            except Exception:
                pass

        if (yp > 0.88 or yp < 0.12) and not is_near_expiry:
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

def generate_fallback_summary(question, flags):
    reasons = []
    for f in flags:
        reasons.append(f"{f['label'].lower()} ({f['detail']})")
    
    reasons_str = ", and ".join(reasons)
    
    summary = (
        f"This market was flagged for investigation due to a {reasons_str}. "
        f"These conditions suggest abnormal trading patterns that could indicate targeted manipulation or sudden sentiment shift by large capital holders (whales). "
        f"Analysts should exercise caution as the current odds may not accurately reflect organic crowd consensus."
    )
    return summary

def get_ai_summary(question, flags, api_key):
    if not api_key:
        return generate_fallback_summary(question, flags)
    
    flag_desc = []
    for f in flags:
        flag_desc.append(f"{f['label']} ({f['detail']}): {f['explanation']}")
    flag_text = "\n- ".join(flag_desc)
    
    prompt = (
        f"You are a financial analyst. Write a beginner-friendly 2-3 sentence summary explaining exactly why this prediction market was flagged as anomalous.\n"
        f"Market Question: {question}\n"
        f"Flagged Reasons:\n- {flag_text}\n\n"
        f"Instructions:\n"
        f"- Be concise: exactly 2 to 3 sentences.\n"
        f"- Explain the anomaly in simple, beginner-friendly terms (e.g. explain what liquidity or velocity spikes mean here).\n"
        f"- Mention specific details/numbers from the flagged reasons.\n"
        f"- Do not use greeting, introductory or concluding remarks. Start directly with the summary."
    )
    
    try:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        data = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.5,
            "max_tokens": 150
        }
        req = urllib.request.Request(
            url, 
            data=json.dumps(data).encode("utf-8"), 
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            resp = json.loads(r.read().decode("utf-8"))
            summary = resp["choices"][0]["message"]["content"].strip()
            return summary
    except Exception as e:
        print(f"AI summary error: {e}", file=sys.stderr)
        return generate_fallback_summary(question, flags)

def add_summary(m, api_key):
    m["ai_summary"] = get_ai_summary(m["question"], m["flags"], api_key)

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

    # Generate AI summaries in parallel
    api_key = os.environ.get("OPENAI_API_KEY")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(add_summary, m, api_key) for m in flagged]
        for fut in futures:
            try:
                fut.result()
            except Exception as e:
                print(f"Error executing ThreadPool: {e}", file=sys.stderr)

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
