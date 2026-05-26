import json
import urllib.request
import urllib.parse
import os
import concurrent.futures
import traceback
import sys
import hashlib
import time
from http.server import BaseHTTPRequestHandler

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY       = os.environ.get("SUPABASE_SERVICE_KEY", "")

POLYMARKET_API = "https://gamma-api.polymarket.com/markets"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
OPENAI_URL     = "https://api.openai.com/v1/chat/completions"

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type": "application/json"
}

def err(msg, tb=""):
    print("HILEX ERROR:", msg, file=sys.stderr)
    print(tb, file=sys.stderr)
    return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": msg, "trace": tb})}

def http_post(url, payload, headers):
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def http_get(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def openai(messages):
    resp = http_post(OPENAI_URL,
        {"model": "gpt-4o", "messages": messages, "temperature": 0.2},
        {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"})
    return resp["choices"][0]["message"]["content"].strip()

def safe_json(text):
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def fetch_polymarket(keyword):
    params = urllib.parse.urlencode({"q": keyword})
    data   = http_get(f"https://gamma-api.polymarket.com/public-search?{params}")
    events = data.get("events", []) if isinstance(data, dict) else []
    slim = []
    for e in events:
        markets = e.get("markets", [])
        for m in markets:
            if not m.get("active") or m.get("closed"):
                continue  # Only keep active, open markets
            try:
                prices = m.get("outcomePrices")
                yes_prob = None
                if isinstance(prices, list) and len(prices) > 0:
                    yes_prob = float(prices[0]) if prices[0] else None
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

def fetch_polymarket_by_slug(slug):
    try:
        url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
        m = http_get(url)
        if isinstance(m, dict) and m.get("id"):
            prices = m.get("outcomePrices")
            yes_prob = None
            if isinstance(prices, list) and len(prices) > 0:
                yes_prob = float(prices[0]) if prices[0] is not None else None
            return {
                "slug": m.get("slug"),
                "question": m.get("question"),
                "yes_prob": yes_prob,
                "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
                "volume": float(m.get("volumeNum") or m.get("volume") or 0),
                "week_change": float(m.get("oneWeekPriceChange") or 0)
            }
    except Exception as e:
        print(f"HILEX: error fetching slug {slug}: {e}", file=sys.stderr)
    return None


def research(query):
    resp = http_post(PERPLEXITY_URL, {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": 'Research this prediction market event. Return ONLY this JSON, nothing else: {"key_facts":[],"current_conditions":[],"historical_context":[],"expert_analysis":[],"momentum_signals":[],"risk_factors":[],"timing_pressure":""}'},
            {"role": "user", "content": query}],
        "temperature": 0.1},
        {"Content-Type": "application/json", "Authorization": f"Bearer {PERPLEXITY_API_KEY}"})
    return safe_json(resp["choices"][0]["message"]["content"])

def features(res):
    return safe_json(openai([
        {"role": "system", "content": 'Convert research to scores. Score each dimension on a scale of -1.0 to 1.0 (float values, e.g. -1.0, -0.5, 0.0, 0.5, 1.0). uncertainty_score must be between 0.0 and 1.0. Return ONLY this JSON structure: {"sentiment_score":0.0,"momentum_score":0.0,"expert_consensus_score":0.0,"historical_similarity_score":0.0,"structural_bias_score":0.0,"uncertainty_score":0.0,"timeline_pressure_score":0.0,"risk_factors":[]}'},
        {"role": "user", "content": json.dumps(res)}]))

def match(query, markets):
    return safe_json(openai([
        {"role": "system", "content": 'Find best matching Polymarket market. Return ONLY JSON. Match: {"match":true,"slug":"...","question":"...","yes_prob":0.0,"liquidity":0.0,"volume":0.0,"week_change":0.0} No match: {"match":false}'},
        {"role": "user", "content": f'Query: {query}\nMarkets: {json.dumps(markets)}'}]))

def misprice(feats, mat):
    scores = [feats.get(k, 0) for k in ["sentiment_score","momentum_score","expert_consensus_score","historical_similarity_score","structural_bias_score","timeline_pressure_score"]]
    score  = sum(scores) / len(scores)
    unc    = feats.get("uncertainty_score", 0.5)
    dir_   = "YES" if score > 0.1 else ("NO" if score < -0.1 else "UNCERTAIN")
    prob   = (score + 1) / 2
    if not mat.get("match"):
        return {"polymarket_matched": False, "our_signal_score": round(score,4),
                "our_direction": dir_, "gap": None, "misprice_flag": False,
                "uncertainty": round(unc,4), "features": feats,
                "polymarket_slug": None, "polymarket_question": None,
                "polymarket_yes_prob": None, "polymarket_liquidity": None,
                "polymarket_volume": None, "polymarket_week_change": None}
    yp_val = mat.get("yes_prob")
    yp  = float(yp_val) if yp_val is not None else 0.0
    liq_val = mat.get("liquidity")
    liq = float(liq_val) if liq_val is not None else 0.0
    vol_val = mat.get("volume")
    vol = float(vol_val) if vol_val is not None else 0.0
    wc_val = mat.get("week_change")
    wc = float(wc_val) if wc_val is not None else 0.0
    
    gap = round(yp - prob, 4)
    cw  = 1 - unc
    return {"polymarket_matched": True, "polymarket_slug": mat.get("slug"),
            "polymarket_question": mat.get("question"), "polymarket_yes_prob": yp,
            "polymarket_liquidity": liq, "polymarket_volume": vol,
            "polymarket_week_change": wc,
            "our_signal_score": round(score,4), "our_direction": dir_,
            "gap": gap, "misprice_flag": abs(gap)>0.20 and liq>5000 and cw>0.4,
            "uncertainty": round(unc,4), "certainty_weight": round(cw,4), "features": feats}

def narrate(query, result, res):
    return safe_json(openai([
        {"role": "system", "content": 'Write market intelligence for HiLEX. Return ONLY: {"signal_summary":"...","market_summary":"..."}. signal_summary: 4-6 sentences of flowing prose. market_summary: 2-3 sentence briefing note. No numbers, no bullets, no recommendations.'},
        {"role": "user", "content": json.dumps({"event": query, "direction": result.get("our_direction"), "features": result.get("features",{}), "research": res})}]))

def log_supabase(query, result, narration):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        http_post(f"{SUPABASE_URL}/rest/v1/prediction_log",
            {"event_description": query, "polymarket_slug": result.get("polymarket_slug"),
             "polymarket_yes_prob": result.get("polymarket_yes_prob"),
             "polymarket_liquidity": result.get("polymarket_liquidity"),
             "our_signal_score": result.get("our_signal_score"),
             "our_direction": result.get("our_direction"),
             "gap": result.get("gap"), "misprice_flag": result.get("misprice_flag"),
             "signal_summary": narration.get("signal_summary"),
             "market_summary": narration.get("market_summary")},
            {"Content-Type": "application/json", "apikey": SUPABASE_KEY,
             "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer": "return=minimal"})
    except Exception:
        pass

def get_cache_path(query):
    clean_q = query.strip().lower()
    h = hashlib.md5(clean_q.encode('utf-8')).hexdigest()
    if os.name == 'nt':
        import tempfile
        return os.path.join(tempfile.gettempdir(), f'hilex_analysis_{h}.json')
    return f'/tmp/hilex_analysis_{h}.json'

def run(query, slug=None):
    print(f"HILEX: starting analysis for: {query} (slug: {slug})", file=sys.stderr)
    
    # Check cache for LLM results
    cache_path = get_cache_path(query)
    cached_data = None
    use_cache = False
    
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
            ts = cached_data.get("timestamp", 0)
            if time.time() - ts < 86400: # 24 hours TTL
                use_cache = True
                print("HILEX: using cached analysis results", file=sys.stderr)
        except Exception as e:
            print(f"HILEX: error reading cache: {e}", file=sys.stderr)
            
    if use_cache and cached_data:
        res = cached_data.get("research")
        feats = cached_data.get("features")
        narration = cached_data.get("narration")
    else:
        print("HILEX: running LLM pipeline...", file=sys.stderr)
        res = research(query)
        feats = features(res)
        
        # Calculate a temporary misprice result solely for the narration input
        scores = [feats.get(k, 0) for k in ["sentiment_score","momentum_score","expert_consensus_score","historical_similarity_score","structural_bias_score","timeline_pressure_score"]]
        score  = sum(scores) / len(scores) if scores else 0.0
        temp_dir = "YES" if score > 0.1 else ("NO" if score < -0.1 else "UNCERTAIN")
        temp_result = {"our_direction": temp_dir, "features": feats}
        
        narration = narrate(query, temp_result, res)
        
        # Save to cache
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "timestamp": time.time(),
                    "research": res,
                    "features": feats,
                    "narration": narration
                }, f)
        except Exception as e:
            print(f"HILEX: error writing cache: {e}", file=sys.stderr)

    # Now fetch live Polymarket odds
    print("HILEX: fetching live Polymarket odds...", file=sys.stderr)
    mat = None
    if slug:
        market_by_slug = fetch_polymarket_by_slug(slug)
        if market_by_slug:
            mat = {
                "match": True,
                **market_by_slug
            }
            print(f"HILEX: found direct slug match: {slug}", file=sys.stderr)
            
    if not mat:
        print("HILEX: falling back to fuzzy text search", file=sys.stderr)
        markets = fetch_polymarket(query)
        mat = match(query, markets)
        
    result = misprice(feats, mat)
    log_supabase(query, result, narration)
    
    # Map backend scores to what the frontend expects
    frontend_scores = {
        "event_score": result.get("our_signal_score", 0.0),
        "news_sentiment_score": feats.get("sentiment_score", 0.0),
        "recent_momentum": feats.get("momentum_score", 0.0),
        "expert_consensus_score": feats.get("expert_consensus_score", 0.0),
        "historical_pattern_match": feats.get("historical_similarity_score", 0.0),
        "structural_edge": feats.get("structural_bias_score", 0.0),
        "time_pressure": feats.get("timeline_pressure_score", 0.0)
    }
    
    return {
        **result, 
        **frontend_scores,
        "event_description": query,
        "signal_summary": narration.get("signal_summary"),
        "market_summary": narration.get("market_summary"),
        "research": res
    }


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
            slug = (body.get("slug") or body.get("polymarket_slug") or "").strip()
            if not query:
                self._respond(400, {"error": "query is required"})
                return
            result = run(query, slug=slug if slug else None)
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
