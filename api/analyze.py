from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.parse
import os
import asyncio
import concurrent.futures
from typing import Optional

# ── ENV VARS (set in Vercel dashboard) ──────────────────────────────────────
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")        # e.g. https://ussceuooawbprpmxcmxg.supabase.co
SUPABASE_KEY       = os.environ.get("SUPABASE_SERVICE_KEY", "")

POLYMARKET_API     = "https://gamma-api.polymarket.com/markets"
PERPLEXITY_API     = "https://api.perplexity.ai/chat/completions"
OPENAI_API         = "https://api.openai.com/v1/chat/completions"


# ── HELPERS ─────────────────────────────────────────────────────────────────

def http_post(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def http_get(url: str, headers: dict = {}) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def openai_chat(messages: list, model: str = "gpt-4o") -> str:
    resp = http_post(
        OPENAI_API,
        {"model": model, "messages": messages, "temperature": 0.2},
        {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}
    )
    return resp["choices"][0]["message"]["content"].strip()

def safe_json(text: str) -> dict:
    """Strip markdown fences then parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ── STEP 1: FETCH POLYMARKET MARKETS ────────────────────────────────────────

def fetch_polymarket(keyword: str) -> list:
    params = urllib.parse.urlencode({
        "active": "true",
        "limit": 20,
        "keyword": keyword
    })
    url  = f"{POLYMARKET_API}?{params}"
    data = http_get(url)
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


# ── STEP 2: PERPLEXITY RESEARCH ─────────────────────────────────────────────

RESEARCH_SYSTEM = """You are a factual research agent. Your job is to find current, verifiable information about a specific event or question to help assess its likelihood.
Adapt your research to the topic — political events need polling and expert analysis, sports need form and statistics, financial events need market data, natural events need forecasts and models.
Focus on:
1. The most recent developments directly related to this event (last 7 days prioritized)
2. Hard data, statistics, or probabilities from official or expert sources
3. Historical precedent — how similar situations have resolved in the past
4. Current momentum — is the situation moving toward or away from this outcome?
5. Key decision makers, influencing factors, or upcoming catalysts
6. Credible expert or analyst opinions
7. Known risk factors that could swing the outcome either way
RULES:
- Only include verifiable, sourced facts
- No speculation, opinion, or social media
- Be specific — dates, numbers, names, percentages where available
- If official probability estimates exist include them
- Do not pad with irrelevant background information
Return ONLY this JSON, nothing else:
{
  "key_facts": [],
  "current_conditions": [],
  "historical_context": [],
  "expert_analysis": [],
  "momentum_signals": [],
  "risk_factors": [],
  "timing_pressure": ""
}"""

def run_perplexity_research(query: str) -> dict:
    resp = http_post(
        PERPLEXITY_API,
        {
            "model": "llama-3.1-sonar-large-128k-online",
            "messages": [
                {"role": "system", "content": RESEARCH_SYSTEM},
                {"role": "user",   "content": f'Event: "{query}"'}
            ],
            "temperature": 0.1
        },
        {"Content-Type": "application/json", "Authorization": f"Bearer {PERPLEXITY_API_KEY}"}
    )
    content = resp["choices"][0]["message"]["content"]
    return safe_json(content)


# ── STEP 3: FEATURE EXTRACTION ───────────────────────────────────────────────

FEATURE_SYSTEM = """You are a Feature Extraction Engine for a prediction-analysis system.
OUTPUT REQUIREMENT: Return ONLY a raw JSON object. Begin with { and end with }.
Convert the research input into NUMERIC prediction features.
RULES:
- Do NOT invent facts. Infer scores ONLY from the input text.
- If a signal cannot be determined, set it to 0.
- Scores MUST range from -1 to +1 unless otherwise noted.
Return ONLY:
{
  "sentiment_score": 0,
  "momentum_score": 0,
  "expert_consensus_score": 0,
  "historical_similarity_score": 0,
  "structural_bias_score": 0,
  "uncertainty_score": 0,
  "timeline_pressure_score": 0,
  "risk_factors": []
}"""

def extract_features(research: dict) -> dict:
    content = openai_chat([
        {"role": "system", "content": FEATURE_SYSTEM},
        {"role": "user",   "content": json.dumps(research)}
    ])
    return safe_json(content)


# ── STEP 4: MARKET MATCHING ──────────────────────────────────────────────────

MATCH_SYSTEM = """You are a market matching engine. Find the single best matching Polymarket market for the event.
Return ONLY raw JSON. Begin with { and end with }.
If a good match exists:
{"match":true,"slug":"...","question":"...","yes_prob":0.00,"liquidity":0.00,"volume":0.00,"week_change":0.00}
If no good match:
{"match":false}
A good match means the market is directly about the same event or outcome. Do not match loosely related markets."""

def match_market(query: str, markets: list) -> dict:
    content = openai_chat([
        {"role": "system", "content": MATCH_SYSTEM},
        {"role": "user",   "content": f'Event: "{query}"\n\nMarkets:\n{json.dumps(markets, indent=2)}'}
    ])
    return safe_json(content)


# ── STEP 5: MISPRICE CALCULATION ─────────────────────────────────────────────

def calculate_misprice(features: dict, match: dict) -> dict:
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
        return {
            "polymarket_matched":  False,
            "polymarket_slug":     None,
            "polymarket_question": None,
            "polymarket_yes_prob": None,
            "polymarket_liquidity":None,
            "polymarket_volume":   None,
            "polymarket_week_change": None,
            "our_signal_score":    round(final_score, 4),
            "our_direction":       direction,
            "gap":                 None,
            "misprice_flag":       False,
            "uncertainty":         round(uncertainty, 4),
            "features":            features
        }

    yes_prob  = float(match.get("yes_prob", 0))
    liquidity = float(match.get("liquidity", 0))
    gap       = round(yes_prob - signal_as_prob, 4)

    # Improved flag: gap > 0.20 AND liquidity > 5000 AND weighted by certainty
    certainty_weight = 1 - uncertainty
    misprice_flag    = (abs(gap) > 0.20) and (liquidity > 5000) and (certainty_weight > 0.4)

    return {
        "polymarket_matched":   True,
        "polymarket_slug":      match.get("slug"),
        "polymarket_question":  match.get("question"),
        "polymarket_yes_prob":  yes_prob,
        "polymarket_liquidity": liquidity,
        "polymarket_volume":    float(match.get("volume", 0)),
        "polymarket_week_change": float(match.get("week_change", 0)),
        "our_signal_score":     round(final_score, 4),
        "our_direction":        direction,
        "gap":                  gap,
        "misprice_flag":        misprice_flag,
        "uncertainty":          round(uncertainty, 4),
        "certainty_weight":     round(certainty_weight, 4),
        "features":             features
    }


# ── STEP 6: NARRATION ────────────────────────────────────────────────────────

NARRATE_SYSTEM = """You are a Market Intelligence Narrator for a prediction analysis platform called HiLEX.
You receive: the event question, a direction label, signal scores, risk factors, and research findings.
Write TWO summaries. Return as JSON with keys "signal_summary" and "market_summary".

SIGNAL SUMMARY (4-6 sentences):
Part 1 — Signal Overview (2 sentences): Summarize what the signals say about direction. Mention the strongest 1-2 drivers by name. Do NOT mention scores or numbers.
Part 2 — Reasoning (2-4 sentences, flows directly after with no break): Cite specific facts from the research. Use "This is supported by..." or "Key evidence includes..." or "Historical precedent suggests...". Mention 2-3 specific data points. If signals conflict acknowledge it naturally.

MARKET SUMMARY (2-3 sentences): Written like a briefing note. Pull specific context from research. Frame as: current dynamics show X, news indicates Y because of Z, historical patterns suggest W. Do NOT mention tool names or make up facts.

RULES:
- No scores or numbers from the signal engine
- No "you should", "we recommend", "bet", "trade"
- No dashes, bullet points, or headers
- Flowing prose only, factual analytical tone
- Use "signals indicate", "data suggests", "evidence points toward"

Return ONLY raw JSON: {"signal_summary":"...","market_summary":"..."}"""

def generate_narration(query: str, result: dict, research: dict) -> dict:
    payload = {
        "event":     query,
        "direction": result.get("our_direction"),
        "features":  result.get("features", {}),
        "research":  research
    }
    content = openai_chat([
        {"role": "system", "content": NARRATE_SYSTEM},
        {"role": "user",   "content": json.dumps(payload)}
    ])
    return safe_json(content)


# ── STEP 7: SUPABASE LOG ─────────────────────────────────────────────────────

def log_to_supabase(query: str, result: dict, narration: dict):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    payload = {
        "event_description":    query,
        "polymarket_slug":      result.get("polymarket_slug"),
        "polymarket_yes_prob":  result.get("polymarket_yes_prob"),
        "polymarket_liquidity": result.get("polymarket_liquidity"),
        "our_signal_score":     result.get("our_signal_score"),
        "our_direction":        result.get("our_direction"),
        "gap":                  result.get("gap"),
        "misprice_flag":        result.get("misprice_flag"),
        "signal_summary":       narration.get("signal_summary"),
        "market_summary":       narration.get("market_summary")
    }
    try:
        http_post(
            f"{SUPABASE_URL}/rest/v1/prediction_log",
            payload,
            {
                "Content-Type": "application/json",
                "apikey":       SUPABASE_KEY,
                "Authorization":f"Bearer {SUPABASE_KEY}",
                "Prefer":       "return=minimal"
            }
        )
    except Exception as e:
        print(f"Supabase log error: {e}")


# ── MAIN PIPELINE ────────────────────────────────────────────────────────────

def run_analysis(query: str) -> dict:
    # Steps 1 & 2 can run in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_markets  = executor.submit(fetch_polymarket, query)
        future_research = executor.submit(run_perplexity_research, query)
        markets  = future_markets.result()
        research = future_research.result()

    # Steps 3 & 4 can also run in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_features = executor.submit(extract_features, research)
        future_match    = executor.submit(match_market, query, markets)
        features = future_features.result()
        match    = future_match.result()

    result    = calculate_misprice(features, match)
    narration = generate_narration(query, result, research)
    log_to_supabase(query, result, narration)

    return {
        **result,
        "event_description": query,
        "signal_summary":    narration.get("signal_summary"),
        "market_summary":    narration.get("market_summary"),
        "research":          research
    }


# ── VERCEL HANDLER ───────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length).decode("utf-8"))
        query  = (body.get("query") or "").strip()

        if not query:
            self._respond(400, {"error": "query is required"})
            return

        try:
            result = run_analysis(query)
            self._respond(200, result)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status: int, data: dict):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *args):
        pass
