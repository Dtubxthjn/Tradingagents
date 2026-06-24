import hashlib
import json
import os
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv(usecwd=True))

import pandas as pd
import yfinance as yf
from fastapi import Depends, FastAPI, Header, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import jinja2

app = FastAPI(title="Stock Analyzer")
ROOT = Path(__file__).parent
tpl = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "templates"), autoescape=True)
executor = ThreadPoolExecutor(max_workers=2)

# ── Auth ────────────────────────────────────────────────────────────────────

_APP_PASSWORD = os.environ.get("APP_PASSWORD")
if not _APP_PASSWORD:
    _APP_PASSWORD = secrets.token_urlsafe(16)
    import logging
    logging.warning("APP_PASSWORD not set. Generated random password: %s", _APP_PASSWORD)

_PASSWORD_HASH = hashlib.sha256(_APP_PASSWORD.encode()).hexdigest()
_TOKENS: dict[str, str] = {}
_TOKENS_LOCK = threading.Lock()


def _create_token() -> str:
    token = secrets.token_urlsafe(32)
    with _TOKENS_LOCK:
        _TOKENS[token] = "user"
    return token


def _validate_token(token: str) -> bool:
    with _TOKENS_LOCK:
        return token in _TOKENS


def _revoke_token(token: str) -> None:
    with _TOKENS_LOCK:
        _TOKENS.pop(token, None)


async def require_auth(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    if not _validate_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return token


async def optional_auth(authorization: str = Header(None)):
    """Allow unauthenticated access to /login and static assets."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    if not _validate_token(token):
        return None
    return token

_WATCHLIST_LOCK = threading.Lock()
_WATCHLIST_TICKERS: list[str] = []
_WATCHLIST_CACHE: dict[str, dict] = {}

POPULAR_STOCKS = [
    # US Stocks
    {"s": "AAPL", "n": "Apple Inc.", "m": "US"},
    {"s": "MSFT", "n": "Microsoft", "m": "US"},
    {"s": "NVDA", "n": "NVIDIA", "m": "US"},
    {"s": "GOOGL", "n": "Alphabet", "m": "US"},
    {"s": "AMZN", "n": "Amazon", "m": "US"},
    {"s": "META", "n": "Meta Platforms", "m": "US"},
    {"s": "TSLA", "n": "Tesla", "m": "US"},
    {"s": "AVGO", "n": "Broadcom", "m": "US"},
    {"s": "JPM", "n": "JPMorgan Chase", "m": "US"},
    {"s": "V", "n": "Visa", "m": "US"},
    {"s": "WMT", "n": "Walmart", "m": "US"},
    {"s": "XOM", "n": "Exxon Mobil", "m": "US"},
    {"s": "UNH", "n": "UnitedHealth", "m": "US"},
    {"s": "JNJ", "n": "Johnson & Johnson", "m": "US"},
    {"s": "PG", "n": "Procter & Gamble", "m": "US"},
    {"s": "MA", "n": "Mastercard", "m": "US"},
    {"s": "HD", "n": "Home Depot", "m": "US"},
    {"s": "ORCL", "n": "Oracle", "m": "US"},
    {"s": "COST", "n": "Costco", "m": "US"},
    {"s": "NFLX", "n": "Netflix", "m": "US"},
    {"s": "ADBE", "n": "Adobe", "m": "US"},
    {"s": "CRM", "n": "Salesforce", "m": "US"},
    {"s": "INTC", "n": "Intel", "m": "US"},
    {"s": "AMD", "n": "AMD", "m": "US"},
    {"s": "PYPL", "n": "PayPal", "m": "US"},
    {"s": "DIS", "n": "Walt Disney", "m": "US"},
    {"s": "NKE", "n": "Nike", "m": "US"},
    {"s": "BA", "n": "Boeing", "m": "US"},
    {"s": "KO", "n": "Coca-Cola", "m": "US"},
    {"s": "PEP", "n": "PepsiCo", "m": "US"},
    {"s": "MCD", "n": "McDonald's", "m": "US"},
    {"s": "ABNB", "n": "Airbnb", "m": "US"},
    {"s": "UBER", "n": "Uber", "m": "US"},
    {"s": "SNAP", "n": "Snap Inc.", "m": "US"},
    # US ETFs
    {"s": "SPY", "n": "SPDR S&P 500 ETF", "m": "US"},
    {"s": "QQQ", "n": "Invesco QQQ (Nasdaq)", "m": "US"},
    {"s": "VOO", "n": "Vanguard S&P 500", "m": "US"},
    {"s": "VTI", "n": "Vanguard Total Stock", "m": "US"},
    {"s": "IVV", "n": "iShares Core S&P 500", "m": "US"},
    {"s": "IWM", "n": "iShares Russell 2000", "m": "US"},
    {"s": "DIA", "n": "SPDR Dow Jones", "m": "US"},
    {"s": "VXUS", "n": "Vanguard Total Intl", "m": "US"},
    {"s": "BND", "n": "Vanguard Total Bond", "m": "US"},
    {"s": "GLD", "n": "SPDR Gold Shares", "m": "US"},
    {"s": "TLT", "n": "iShares 20+ Yr Treasury", "m": "US"},
    {"s": "EEM", "n": "iShares Emerging Markets", "m": "US"},
    {"s": "XLF", "n": "Financial Sector ETF", "m": "US"},
    {"s": "XLK", "n": "Tech Sector ETF", "m": "US"},
    {"s": "XLE", "n": "Energy Sector ETF", "m": "US"},
    {"s": "XLV", "n": "Healthcare Sector ETF", "m": "US"},
    # India Stocks
    {"s": "RELIANCE.NS", "n": "Reliance Industries", "m": "INDIA"},
    {"s": "TCS.NS", "n": "Tata Consultancy Services", "m": "INDIA"},
    {"s": "INFY.NS", "n": "Infosys", "m": "INDIA"},
    {"s": "HDFCBANK.NS", "n": "HDFC Bank", "m": "INDIA"},
    {"s": "ICICIBANK.NS", "n": "ICICI Bank", "m": "INDIA"},
    {"s": "BHARTIARTL.NS", "n": "Bharti Airtel", "m": "INDIA"},
    {"s": "ITC.NS", "n": "ITC Limited", "m": "INDIA"},
    {"s": "SBIN.NS", "n": "State Bank of India", "m": "INDIA"},
    {"s": "BAJFINANCE.NS", "n": "Bajaj Finance", "m": "INDIA"},
    {"s": "HINDUNILVR.NS", "n": "Hindustan Unilever", "m": "INDIA"},
    {"s": "NTPC.NS", "n": "NTPC Ltd", "m": "INDIA"},
    {"s": "POWERGRID.NS", "n": "Power Grid Corp", "m": "INDIA"},
    {"s": "MARUTI.NS", "n": "Maruti Suzuki", "m": "INDIA"},
    {"s": "TATAMOTORS.NS", "n": "Tata Motors", "m": "INDIA"},
    {"s": "TITAN.NS", "n": "Titan Company", "m": "INDIA"},
    {"s": "ASIANPAINT.NS", "n": "Asian Paints", "m": "INDIA"},
    {"s": "WIPRO.NS", "n": "Wipro", "m": "INDIA"},
    {"s": "HCLTECH.NS", "n": "HCL Technologies", "m": "INDIA"},
    {"s": "SUNPHARMA.NS", "n": "Sun Pharma", "m": "INDIA"},
    {"s": "AXISBANK.NS", "n": "Axis Bank", "m": "INDIA"},
    {"s": "KOTAKBANK.NS", "n": "Kotak Mahindra Bank", "m": "INDIA"},
    {"s": "LT.NS", "n": "Larsen & Toubro", "m": "INDIA"},
    {"s": "HAL.NS", "n": "Hindustan Aeronautics", "m": "INDIA"},
    {"s": "ADANIENT.NS", "n": "Adani Enterprises", "m": "INDIA"},
    {"s": "ADANIGREEN.NS", "n": "Adani Green Energy", "m": "INDIA"},
    # Crypto
    {"s": "BTC-USD", "n": "Bitcoin", "m": "CRYPTO"},
    {"s": "ETH-USD", "n": "Ethereum", "m": "CRYPTO"},
    {"s": "SOL-USD", "n": "Solana", "m": "CRYPTO"},
    {"s": "XRP-USD", "n": "XRP", "m": "CRYPTO"},
    {"s": "DOGE-USD", "n": "Dogecoin", "m": "CRYPTO"},
]

SYSTEM_PROMPT = """You are a Senior Quantitative Equity Analyst and Financial Engineer at a tier-1 investment bank. Your mandate is to provide institutional-grade, data-driven, objective stock analyses. You must never offer generalized advice. Your output must strictly adhere to professional financial frameworks, maintaining a clinical, objective, and authoritative tone.

## 1. Core Mandate & Execution Flow
For any stock ticker provided, you must execute a comprehensive Three-Layer Analysis Framework:
   1. Quantitative & Technical Layer: Analyze recent price action, volatility, and volume trends.
   2. Fundamental & Valuation Layer: Evaluate profitability, debt health, and multiple-based valuations.
   3. Macro & Sentiment Layer: Factor in recent market catalysts, sector headwinds, and risk metrics.

Based on the synthesis of these layers, you will issue a definitive Actionable Rating (BUY, STRONG BUY, HOLD, SELL, or STRONG SELL) accompanied by a rigorous, data-backed justification.

## 2. Required Analysis Sub-Components
### A. Executive Summary & Signal Rating
* Ticker/Company: Provide the confirmed ticker and company name.
* The Signal: State the final rating boldly (BUY / STRONG BUY / HOLD / SELL / STRONG SELL).
* Target Horizon: Specify a 12-month target time frame.
* Risk Profile: Classify as Low, Medium, High, or Speculative.

### B. Layer 1: Quantitative & Technical Matrix
* Momentum Indicators: Evaluate the relationship between the current spot price and its Moving Averages (50-day EMA and 200-day EMA) to confirm trend direction.
* Overbought/Oversold Conditions: Evaluate the 14-day Relative Strength Index (RSI).
* Volume & Volatility: Interpret the volume-weighted average price (VWAP) and Average True Range (ATR) to measure institutional conviction and volatility risk.

### C. Layer 2: Fundamental Integrity & Valuation
* Profitability Metrics: Assess Return on Equity (ROE), Return on Invested Capital (ROIC), and Operating Margin trends.
* Solvency & Liquidity: Evaluate balance sheet durability using the Debt-to-Equity ratio, Interest Coverage Ratio, and Current Ratio.
* Relative Valuation: Compare Forward P/E, PEG Ratio, and EV/EBITDA against its sector median and historical averages to determine if it is undervalued or overvalued.

### D. Layer 3: Risk Catalysts & Microeconomics
* Headwinds/Tailwinds: Detail structural sector trends, competitive positioning (moat strength), and macroeconomic exposure (e.g., interest rate sensitivity).
* Downside Risks: Explicitly state the top 2 catalysts that could invalidate your thesis (e.g., regulatory shifts, margin compression, supply chain bottlenecks).

### E. Professional Justification Matrix ("The Why")
You must synthesize the technicals and fundamentals into a cohesive argument.
* If BUY, prove why the market is mispricing the asset and identify the upcoming catalyst for re-rating.
* If SELL, prove structural decay, valuation overextension, or technical breakdown.
* If HOLD, outline the equilibrium parameters keeping the stock range-bound.

## 3. Output Format Constraints (Strict JSON Schema)
You MUST return your response strictly as a JSON object matching the following structure. Do not wrap the JSON in Markdown backticks. Omit any conversational filler.

{
  "ticker": "STRING",
  "company_name": "STRING",
  "analysis_timestamp": "ISO_8601_TIMESTAMP",
  "recommendation": {
    "signal": "STRONG_BUY | BUY | HOLD | SELL | STRONG_SELL",
    "horizon": "12 Months",
    "risk_level": "LOW | MEDIUM | HIGH | SPECULATIVE",
    "conviction_score": 0.0
  },
  "quantitative_analysis": {
    "trend_bias": "BULLISH | BEARISH | NEUTRAL",
    "rsi_interpretation": "STRING",
    "moving_average_alignment": "STRING",
    "volatility_regime": "LOW | NORMAL | HIGH"
  },
  "fundamental_analysis": {
    "valuation_stance": "UNDERVALUED | FAIR_VALUE | OVERVALUED",
    "balance_sheet_health": "STRONG | STABLE | DISTRESSED",
    "growth_trajectory": "ACCELERATING | STABLE | DECELERATING"
  },
  "investment_thesis": {
    "bull_case_drivers": ["STRING_1", "STRING_2"],
    "bear_case_risks": ["STRING_1", "STRING_2"],
    "detailed_justification": "STRING"
  }
}

## 4. Edge-Case Guardrails
1. Zero Hallucination Policy: If incoming data payloads lack critical fundamental metrics, state "DATA_UNAVAILABLE" inside that specific JSON key rather than manufacturing figures.
2. Context Maintenance: Base your analysis purely on factual financial metrics and market data provided. Do not invent corporate news or rumors.
3. Every JSON output must automatically be assumed to append a legal disclaimer. Do not output this disclaimer in the JSON itself."""


def compute_rsi(series: pd.Series, period: int = 14) -> float | None:
    if len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain.iloc[-1] / avg_loss.iloc[-1] if avg_loss.iloc[-1] != 0 else 100
    return round(100 - (100 / (1 + rs)), 1)


def compute_ema(series: pd.Series, period: int) -> float | None:
    if len(series) < period:
        return None
    return round(series.ewm(span=period, adjust=False).mean().iloc[-1], 2)


def compute_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    if len(df) < period:
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return round(tr.rolling(window=period).mean().iloc[-1], 2)


def fetch_financial_data(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.info or {}
    hist = stock.history(period="1y")

    data = {
        "ticker": ticker.upper(),
        "company_name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
    }

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
    change_pct = info.get("regularMarketChangePercent")
    data["current_price"] = round(price, 2) if price else None
    data["previous_close"] = round(prev_close, 2) if prev_close else None
    data["change_pct"] = round(change_pct, 2) if change_pct else None

    if not hist.empty:
        close = hist["Close"]
        data["52w_high"] = round(float(close.max()), 2)
        data["52w_low"] = round(float(close.min()), 2)
        data["ema_50"] = compute_ema(close, 50)
        data["ema_200"] = compute_ema(close, 200)
        data["rsi_14"] = compute_rsi(close)
        data["atr_14"] = compute_atr(hist)
        data["avg_volume_20d"] = int(hist["Volume"].tail(20).mean())
    else:
        data["52w_high"] = data["52w_low"] = data["ema_50"] = data["ema_200"] = None
        data["rsi_14"] = data["atr_14"] = data["avg_volume_20d"] = None

    fin = info
    data["forward_pe"] = fin.get("forwardPE")
    data["trailing_pe"] = fin.get("trailingPE")
    data["peg_ratio"] = fin.get("pegRatio")
    data["ev_to_ebitda"] = fin.get("enterpriseToEbitda")
    data["price_to_book"] = fin.get("priceToBook")
    data["debt_to_equity"] = fin.get("debtToEquity")
    data["current_ratio"] = fin.get("currentRatio")
    data["interest_coverage"] = fin.get("interestCoverage")
    data["roe"] = fin.get("returnOnEquity")
    data["roic"] = fin.get("returnOnInvestedCapital")
    data["operating_margin"] = fin.get("operatingMargins")
    data["revenue_growth"] = fin.get("revenueGrowth")
    data["earnings_growth"] = fin.get("earningsQuarterlyGrowth")
    data["dividend_yield"] = fin.get("dividendYield")
    data["beta"] = fin.get("beta")
    data["short_ratio"] = fin.get("shortRatio")

    return data


def call_llm(system_prompt: str, user_message: str) -> dict | None:
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ.get("GROQ_API_KEY"),
        base_url="https://api.groq.com/openai/v1",
    )
    resp = client.chat.completions.create(
        model=os.environ.get("ANALYSIS_MODEL", "llama-3.3-70b-versatile"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return json.loads(content)


def build_user_message(data: dict) -> str:
    return f"""Analyze the following stock ticker based on the provided financial data.

Ticker: {data['ticker']}
Company: {data['company_name']}
Sector: {data.get('sector', 'N/A')} | Industry: {data.get('industry', 'N/A')}
Market Cap: {data.get('market_cap', 'N/A')}
Current Price: ${data['current_price']} | Change: {data.get('change_pct', 'N/A')}%
52W Range: ${data.get('52w_low', 'N/A')} - ${data.get('52w_high', 'N/A')}
50-day EMA: {data.get('ema_50', 'N/A')} | 200-day EMA: {data.get('ema_200', 'N/A')}
RSI(14): {data.get('rsi_14', 'N/A')} | ATR(14): {data.get('atr_14', 'N/A')}
Avg Volume (20d): {data.get('avg_volume_20d', 'N/A')}

Forward P/E: {data.get('forward_pe', 'N/A')} | Trailing P/E: {data.get('trailing_pe', 'N/A')}
PEG Ratio: {data.get('peg_ratio', 'N/A')} | EV/EBITDA: {data.get('ev_to_ebitda', 'N/A')}
Price/Book: {data.get('price_to_book', 'N/A')}
Debt/Equity: {data.get('debt_to_equity', 'N/A')} | Current Ratio: {data.get('current_ratio', 'N/A')}
Interest Coverage: {data.get('interest_coverage', 'N/A')}
ROE: {data.get('roe', 'N/A')} | ROIC: {data.get('roic', 'N/A')} | Op Margin: {data.get('operating_margin', 'N/A')}
Revenue Growth: {data.get('revenue_growth', 'N/A')} | Earnings Growth: {data.get('earnings_growth', 'N/A')}
Dividend Yield: {data.get('dividend_yield', 'N/A')} | Beta: {data.get('beta', 'N/A')}
Short Ratio: {data.get('short_ratio', 'N/A')}"""


# ── Auth Routes ─────────────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return tpl.get_template("login.html").render()


@app.post("/api/login")
async def login(password: str = Query(...)):
    if hashlib.sha256(password.encode()).hexdigest() != _PASSWORD_HASH:
        raise HTTPException(status_code=401, detail="Invalid password")
    token = _create_token()
    return JSONResponse({"token": token})


@app.post("/api/logout")
async def logout(token: str = Depends(require_auth)):
    _revoke_token(token)
    return JSONResponse({"ok": True})


# ── API Routes (protected) ──────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    return tpl.get_template("index.html").render()


@app.get("/api/stocks")
async def stocks(_=Depends(require_auth)):
    return JSONResponse(POPULAR_STOCKS)


@app.get("/api/stocks/search")
async def search_stocks(q: str = Query(..., min_length=1, max_length=50), _=Depends(require_auth)):
    try:
        s = yf.Search(q)
        results = []
        seen = set()
        for quote in (s.quotes or []):
            sym = quote.get("symbol", "")
            name = quote.get("shortname") or quote.get("longname") or ""
            exch = quote.get("exchange", "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            market = "US"
            if exch in ("NSI", "BSE"):
                market = "INDIA"
            elif exch in ("EBS",):
                market = "OTHER"
            results.append({"s": sym, "n": name, "m": market, "e": exch})
        return JSONResponse(results[:20])
    except Exception:
        return JSONResponse([])


@app.get("/api/price")
async def price(ticker: str = Query("AAPL"), period: str = Query("1y"), _=Depends(require_auth)):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            return JSONResponse({"error": f"No data for {ticker}"}, status_code=404)
        info = stock.info or {}
        close_series = df["Close"]
        return JSONResponse({
            "dates": [d.strftime("%Y-%m-%d") for d in df.index],
            "close": [round(float(x), 2) for x in close_series.values],
            "volume": [int(x) for x in df["Volume"].values],
            "company": info.get("longName") or info.get("shortName") or ticker,
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "change_pct": info.get("regularMarketChangePercent"),
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector") or "",
            "52w_high": round(float(close_series.max()), 2),
            "52w_low": round(float(close_series.min()), 2),
            "rsi_14": compute_rsi(close_series),
            "beta": info.get("beta"),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/analyze")
async def analyze(ticker: str = Query("AAPL"), _=Depends(require_auth)):
    try:
        data = fetch_financial_data(ticker)
        user_msg = build_user_message(data)
        llm_result = call_llm(SYSTEM_PROMPT, user_msg)
        if llm_result:
            llm_result["price_snapshot"] = {
                "current_price": data["current_price"],
                "change_pct": data["change_pct"],
                "company_name": data["company_name"],
            }
        return JSONResponse(llm_result or {"error": "LLM returned empty response"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Watchlist ───────────────────────────────────────────────────────────────


def quick_price(ticker: str):
    try:
        s = yf.Ticker(ticker)
        info = s.info or {}
        p = info.get("currentPrice") or info.get("regularMarketPrice")
        if p is None:
            return None
        return {
            "company": info.get("longName") or info.get("shortName") or ticker,
            "current_price": p,
            "change_pct": info.get("regularMarketChangePercent"),
        }
    except Exception:
        return None


def analyze_one(ticker: str):
    with _WATCHLIST_LOCK:
        _WATCHLIST_CACHE[ticker] = {**_WATCHLIST_CACHE.get(ticker, {}), "status": "running"}
    try:
        data = fetch_financial_data(ticker)
        user_msg = build_user_message(data)
        result = call_llm(SYSTEM_PROMPT, user_msg)
        rec = (result or {}).get("recommendation", {})
        entry = {
            "status": "done",
            "company": data["company_name"],
            "current_price": data["current_price"],
            "change_pct": data["change_pct"],
            "action": rec.get("signal", "").replace("_", " ") if rec.get("signal") else "",
            "confidence": rec.get("conviction_score"),
            "risk_level": rec.get("risk_level"),
            "last_updated": datetime.now().strftime("%H:%M:%S"),
        }
        with _WATCHLIST_LOCK:
            _WATCHLIST_CACHE[ticker] = entry
    except Exception as e:
        with _WATCHLIST_LOCK:
            pi = quick_price(ticker)
            _WATCHLIST_CACHE[ticker] = {
                "status": "error",
                "error": str(e)[:200],
                "last_updated": datetime.now().strftime("%H:%M:%S"),
                **(pi or {}),
            }


@app.get("/api/watchlist")
async def watchlist_get(_=Depends(require_auth)):
    items = []
    with _WATCHLIST_LOCK:
        tickers = list(_WATCHLIST_TICKERS)
    for t in tickers:
        entry = {"ticker": t}
        cached = _WATCHLIST_CACHE.get(t, {})
        entry.update(cached)
        if "company" not in entry or not entry.get("current_price"):
            pi = quick_price(t)
            if pi:
                entry.update(pi)
        items.append(entry)
    return JSONResponse(items)


@app.post("/api/watchlist/add")
async def watchlist_add(ticker: str = Query(...), _=Depends(require_auth)):
    t = ticker.upper().strip()
    with _WATCHLIST_LOCK:
        if t not in _WATCHLIST_TICKERS:
            _WATCHLIST_TICKERS.append(t)
            _WATCHLIST_CACHE[t] = {"status": "pending"}
    executor.submit(analyze_one, t)
    return JSONResponse({"ok": True, "ticker": t})


@app.post("/api/watchlist/remove")
async def watchlist_remove(ticker: str = Query(...), _=Depends(require_auth)):
    t = ticker.upper().strip()
    with _WATCHLIST_LOCK:
        if t in _WATCHLIST_TICKERS:
            _WATCHLIST_TICKERS.remove(t)
        _WATCHLIST_CACHE.pop(t, None)
    return JSONResponse({"ok": True})


@app.post("/api/watchlist/refresh")
async def watchlist_refresh(ticker: str = Query(None), _=Depends(require_auth)):
    with _WATCHLIST_LOCK:
        targets = [ticker.upper().strip()] if ticker else list(_WATCHLIST_TICKERS)
    for t in targets:
        executor.submit(analyze_one, t)
    return JSONResponse({"ok": True, "count": len(targets)})


@app.post("/api/watchlist/set")
async def watchlist_set(tickers: str = Query(...), _=Depends(require_auth)):
    new_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    with _WATCHLIST_LOCK:
        _WATCHLIST_TICKERS.clear()
        _WATCHLIST_CACHE.clear()
        for t in new_list:
            _WATCHLIST_TICKERS.append(t)
            _WATCHLIST_CACHE[t] = {"status": "pending"}
    for t in new_list:
        executor.submit(analyze_one, t)
    return JSONResponse({"ok": True, "count": len(new_list)})


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
