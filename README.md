# HiLEX Prediction Analysis API

Single Vercel endpoint replacing the Make.com prediction market pipeline.

## Endpoint
`POST /api/analyze`

### Request
```json
{ "query": "Will the Fed cut rates in June 2026?" }
```

### Response
```json
{
  "event_description": "...",
  "polymarket_matched": true,
  "polymarket_slug": "...",
  "polymarket_question": "...",
  "polymarket_yes_prob": 0.62,
  "polymarket_liquidity": 45000,
  "polymarket_volume": 120000,
  "polymarket_week_change": 0.04,
  "our_signal_score": 0.21,
  "our_direction": "YES",
  "gap": 0.31,
  "misprice_flag": true,
  "uncertainty": 0.3,
  "certainty_weight": 0.7,
  "signal_summary": "...",
  "market_summary": "...",
  "research": { ... },
  "features": { ... }
}
```

## Deployment

1. Push this folder to a GitHub repo
2. Import to Vercel → New Project
3. Set these environment variables in Vercel dashboard (Settings → Environment Variables):

| Variable | Description |
|---|---|
| `PERPLEXITY_API_KEY` | Perplexity API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `SUPABASE_URL` | e.g. https://ussceuooawbprpmxcmxg.supabase.co |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |

4. Deploy — the endpoint will be live at `https://your-project.vercel.app/api/analyze`

## What it does vs Make.com

| Step | Make.com | This script |
|---|---|---|
| Polymarket fetch | HTTP module | `fetch_polymarket()` — same Gamma API |
| Research | Perplexity module | `run_perplexity_research()` — same prompts |
| Feature extraction | OpenAI module | `extract_features()` — same scoring logic |
| Market matching | OpenAI module | `match_market()` — same matching logic |
| Misprice calc | JS code module | `calculate_misprice()` — improved: certainty-weighted flag |
| Narration | OpenAI module | `generate_narration()` — same narrator prompts |
| Supabase log | Supabase module | `log_to_supabase()` — same table/fields |

## Improvements over Make.com

- Polymarket fetch + Perplexity research run in parallel (faster)
- Feature extraction + market matching run in parallel (faster)
- Misprice flag is certainty-weighted: `gap > 0.20 AND liquidity > 5000 AND certainty_weight > 0.4`
  - Prevents false flags on high-uncertainty markets
- No polling intervals or Make.com execution limits
- Single HTTP call from frontend — no webhook chain

## Frontend integration

Replace your existing Make.com webhook call with:

```javascript
const res = await fetch("https://your-project.vercel.app/api/analyze", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ query: userSearchQuery })
});
const data = await res.json();
```
