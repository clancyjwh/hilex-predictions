import json
import urllib.request
import urllib.parse
import os
import concurrent.futures
from http.server import BaseHTTPRequestHandler

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY       = os.environ.get("SUPABASE_SERVICE_KEY", "")

POLYMARKET_API = "https://gamma-api.polymarket.com/markets"
PERPLEXITY_API = "https://api.perplexity.ai/chat/completions"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


def http_post(url, payload, headers):
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def http_get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def openai_chat(messages):
    resp = http_post(
        OPENAI_API_URL,
        {"model": "gpt-4o", "messages": messages, "temperature": 0.2},
        {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}
    )
    return resp["choices"][0]["message"]["content"].strip()

def safe_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def fetch_polymarket(keyword):
    params = urllib.parse.urlencode({"active": "true", "limit": 20, "keyword": keyword})
    data   = http_get(f"{POLYMARKET_API}?{params}")
    markets = data if isinstance(data, list) else data.get("markets", [])
    slim = []
    for m in markets:
        try:
            prices = json.loads(m.get("outcomePrices", "[null]"))
            slim.append({
                "slug":        m.get("slug"),
                "question":    m.get("question"),
                "yes_prob":    float(prices[0]) if prices[0] is not None else None,
                "liquidity":   m.get("liquidityNum", 0),
                "volume":      m.get("volumeNum", 0),
                "week_change": m.get("oneWeekPriceChange", 0)
            })
        except Exception:
            continue
    return slim

def run_perplexity(query):
    resp = http_post(
        PERPLEXITY_API,
        {
            "model": "llama-3.1-sonar-large-128k-online",
            "messages": [
                {"role": "system", "content": 'You are a factual research agent. Research the following prediction market event and return ONLY this JSON with no other text: {"key_facts": [], "current_conditions": [], "historical_context": [], "expert_analysis": [], "momentum_signals": [], "risk_factors": [], "timing_pressure": ""}'},
                {"role": "user", "content": f'Event: "{query}"'}
            ],
            "temperature": 0.1
        },
        {"Content-Type": "application/json", "Authorization": f"Bearer {PERPLEXITY_API_KEY}"}
    )
    return safe_json(resp["choices"][0]["message"]["content"])

def extract_features(research):
    content = openai_chat([
        {"role": "system", "content": 'You are a feature extraction engine. Convert research into numeric scores. Return ONLY this JSON: {"sentiment_score": 0, "momentum_score": 0, "expert_consensus_score": 0, "historical_similarity_score": 0, "structural_bias_score": 0, "uncertainty_score": 0, "timeline_pressure_score": 0, "risk_factors": []}. Scores range -1 to +1 except uncertainty_score which is 0 to 1.'},
        {"role": "user", "content": json.dumps(research)}
    ])
    return safe_json(content)

def match_market(query, markets):
    content = openai_chat([
        {"role": "system", "content": 'You are a market matching engine. Find the best matching Polymarket market. Return ONLY raw JSON. If match: {"match":true,"slug":"...","question":"...","yes_prob":0.00,"liquidity":0.00,"volume":0.00,"week_change":0.00} If no match: {"match":false}'},
        {"role": "user", "content": f'Event: "{query}"\n\nMarkets:\n{json.dumps(markets)}'}
    ])
    return safe_json(content)

def calculate_misprice(features, match):
    scores = [
        features.get("sentiment_score", 0),
        features.get("momentum_score", 0),
        features.get("expert_consensus_score", 0),
        features.get("historical_similarity_score", 0),
        features.get("structural_bias_score", 0),
        features.get("timeline_pressure_score", 0),
    ]
    final_score    = sum(scores) / len(scores)
    uncertainty    = features.get("uncertainty_score", 0.5)
    direction      = "YES" if final_score > 0.1 else ("NO" if final_score < -0.1 else "UNCERTAIN")
    signal_as_prob = (final_score + 1) / 2

    if not match.get("match"):
        return {"polymarket_matched": False, "polymarket_slug": None, "polymarket_question": None,
                "polymarket_yes_prob": None, "polymarket_liquidity": None, "polymarket_volume": None,
                "polymarket_week_change": None, "our_signal_score": round(final_score, 4),
                "our_direction": direction, "gap": None, "misprice_flag": False,
                "uncertainty": round(uncertainty, 4), "features": features}

    yes_prob         = float(match.get("yes_prob", 0))
    liquidity        = float(match.get("liquidity", 0))
    gap              = round(yes_prob - signal_as_prob, 4)
    certainty_weight = 1 - uncertainty
    misprice_flag    = abs(gap) > 0.20 and liquidity > 5000 and certainty_weight > 0.4

    return {"polymarket_matched": True, "polymarket_slug": match.get("slug"),
            "polymarket_question": match.get("question"), "polymarket_yes_prob": yes_prob,
            "polymarket_liquidity": liquidity, "polymarket_volume": float(match.get("volume", 0)),
            "polymarket_week_change": float(match.get("week_change", 0)),
            "our_signal_score": round(final_score, 4), "our_direction": direction,
            "gap": gap, "misprice_flag": misprice_flag,
            "uncertainty": round(uncertainty, 4), "certainty_weight": round(certainty_weight, 4),
            "features": features}

def generate_narration(query, result, research):
    payload = {"event": query, "direction": result.get("our_direction"),
               "features": result.get("features", {}), "research": research}
    content = openai_chat([
        {"role": "system", "content": 'You are a Market Intelligence Narrator for HiLEX. Write two summaries. Return ONLY: {"signal_summary":"...","market_summary":"..."}. signal_summary: 4-6 sentences of flowing prose about signals and evidence. market_summary: 2-3 sentence briefing note. No scores, no bullet points, no recommendations.'},
        {"role": "user", "content": json.dumps(payload)}
    ])
    return safe_json(content)

def log_to_supabase(query, result, narration):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        http_post(
            f"{SUPABASE_URL}/rest/v1/prediction_log",
            {"event_description": query, "polymarket_slug": result.get("polymarket_slug"),
             "polymarket_yes_prob": result.get("polymarket_yes_prob"),
             "polymarket_liquidity": result.get("polymarket_liquidity"),
             "our_signal_score": result.get("our_signal_score"),
             "our_direction": result.get("our_direction"),
             "gap": result.get("gap"), "misprice_flag": result.get("misprice_flag"),
             "signal_summary": narration.get("signal_summary"),
             "market_summary": narration.get("market_summary")},
            {"Content-Type": "application/json", "apikey": SUPABASE_KEY,
             "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer": "return=minimal"}
        )
    except Exception as e:
        print(f"Supabase error: {e}")

def run_analysis(query):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_markets  = ex.submit(fetch_polymarket, query)
        f_research = ex.submit(run_perplexity, query)
        markets  = f_markets.result()
        research = f_research.result()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_features = ex.submit(extract_features, research)
        f_match    = ex.submit(match_market, query, markets)
        features = f_features.result()
        match    = f_match.result()

    result    = calculate_misprice(features, match)
    narration = generate_narration(query, result, research)
    log_to_supabase(query, result, narration)

    return {**result, "event_description": query,
            "signal_summary": narration.get("signal_summary"),
            "market_summary": narration.get("market_summary"),
            "research": research}


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length).decode("utf-8"))
            query  = (body.get("query") or "").strip()
            if not query:
                self._respond(400, {"error": "query is required"})
                return
            result = run_analysis(query)
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
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *args):
        pass
