import json
import urllib.request
import urllib.parse
import os
import concurrent.futures
import traceback

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY       = os.environ.get("SUPABASE_SERVICE_KEY", "")

POLYMARKET_API = "https://gamma-api.polymarket.com/markets"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
OPENAI_URL     = "https://api.openai.com/v1/chat/completions"


def http_post(url, payload, headers):
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def http_get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
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
    params = urllib.parse.urlencode({"active": "true", "limit": 20, "keyword": keyword})
    data   = http_get(f"{POLYMARKET_API}?{params}")
    markets = data if isinstance(data, list) else data.get("markets", [])
    slim = []
    for m in markets:
        try:
            prices = json.loads(m.get("outcomePrices", "[null]"))
            slim.append({"slug": m.get("slug"), "question": m.get("question"),
                         "yes_prob": float(prices[0]) if prices[0] else None,
                         "liquidity": m.get("liquidityNum", 0), "volume": m.get("volumeNum", 0),
                         "week_change": m.get("oneWeekPriceChange", 0)})
        except Exception:
            continue
    return slim

def research(query):
    resp = http_post(PERPLEXITY_URL, {
        "model": "llama-3.1-sonar-large-128k-online",
        "messages": [
            {"role": "system", "content": 'Research this prediction market event. Return ONLY this JSON, nothing else: {"key_facts":[],"current_conditions":[],"historical_context":[],"expert_analysis":[],"momentum_signals":[],"risk_factors":[],"timing_pressure":""}'},
            {"role": "user", "content": query}],
        "temperature": 0.1},
        {"Content-Type": "application/json", "Authorization": f"Bearer {PERPLEXITY_API_KEY}"})
    return safe_json(resp["choices"][0]["message"]["content"])

def features(res):
    return safe_json(openai([
        {"role": "system", "content": 'Convert research to scores. Return ONLY: {"sentiment_score":0,"momentum_score":0,"expert_consensus_score":0,"historical_similarity_score":0,"structural_bias_score":0,"uncertainty_score":0,"timeline_pressure_score":0,"risk_factors":[]}'},
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
    yp  = float(mat.get("yes_prob", 0))
    liq = float(mat.get("liquidity", 0))
    gap = round(yp - prob, 4)
    cw  = 1 - unc
    return {"polymarket_matched": True, "polymarket_slug": mat.get("slug"),
            "polymarket_question": mat.get("question"), "polymarket_yes_prob": yp,
            "polymarket_liquidity": liq, "polymarket_volume": float(mat.get("volume",0)),
            "polymarket_week_change": float(mat.get("week_change",0)),
            "our_signal_score": round(score,4), "our_direction": dir_,
            "gap": gap, "misprice_flag": abs(gap)>0.20 and liq>5000 and cw>0.4,
            "uncertainty": round(unc,4), "certainty_weight": round(cw,4), "features": feats}

def narrate(query, result, res):
    return safe_json(openai([
        {"role": "system", "content": 'Write market intelligence for HiLEX. Return ONLY: {"signal_summary":"...","market_summary":"..."}. signal_summary: 4-6 sentences of flowing prose about signals and evidence. market_summary: 2-3 sentence briefing note. No numbers from scoring, no bullet points, no recommendations.'},
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

def run(query):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fm = ex.submit(fetch_polymarket, query)
        fr = ex.submit(research, query)
        markets = fm.result()
        res     = fr.result()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        ff  = ex.submit(features, res)
        fma = ex.submit(match, query, markets)
        feats = ff.result()
        mat   = fma.result()
    result    = misprice(feats, mat)
    narration = narrate(query, result, res)
    log_supabase(query, result, narration)
    return {**result, "event_description": query,
            "signal_summary": narration.get("signal_summary"),
            "market_summary": narration.get("market_summary"),
            "research": res}


CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type": "application/json"
}

def handler(request):
    if request.method == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}

    if request.method != "POST":
        return {"statusCode": 405, "headers": CORS, "body": json.dumps({"error": "POST only"})}

    try:
        body  = json.loads(request.body if isinstance(request.body, str) else request.body.decode())
        query = (body.get("query") or "").strip()
        if not query:
            return {"statusCode": 400, "headers": CORS, "body": json.dumps({"error": "query is required"})}
        result = run(query)
        return {"statusCode": 200, "headers": CORS, "body": json.dumps(result)}
    except Exception as e:
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e), "trace": traceback.format_exc()})}
