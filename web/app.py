import asyncio
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
import jinja2

sys.path.insert(0, str(Path(__file__).parent.parent))
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

app = FastAPI(title="TradingAgents Web")
ROOT = Path(__file__).parent
tpl = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "templates"), autoescape=True)
executor = ThreadPoolExecutor(max_workers=1)

POPULAR_STOCKS = [
    {"s": "AAPL", "n": "Apple Inc.", "m": "US"},
    {"s": "MSFT", "n": "Microsoft", "m": "US"},
    {"s": "NVDA", "n": "NVIDIA", "m": "US"},
    {"s": "GOOGL", "n": "Alphabet (Google)", "m": "US"},
    {"s": "AMZN", "n": "Amazon", "m": "US"},
    {"s": "META", "n": "Meta Platforms", "m": "US"},
    {"s": "TSLA", "n": "Tesla", "m": "US"},
    {"s": "SPY", "n": "S&P 500 ETF", "m": "US"},
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
    {"s": "BTC-USD", "n": "Bitcoin", "m": "CRYPTO"},
    {"s": "ETH-USD", "n": "Ethereum", "m": "CRYPTO"},
]


def get_stock_history(ticker: str, period: str = "1y"):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            return None
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        info = stock.info or {}
        return {
            "dates": dates,
            "open": [round(float(x), 2) for x in df["Open"].values],
            "high": [round(float(x), 2) for x in df["High"].values],
            "low": [round(float(x), 2) for x in df["Low"].values],
            "close": [round(float(x), 2) for x in df["Close"].values],
            "volume": [int(x) for x in df["Volume"].values],
            "company": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector") or "",
            "industry": info.get("industry") or "",
            "market_cap": info.get("marketCap"),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "previous_close": info.get("previousClose") or info.get("regularMarketPreviousClose"),
            "change_pct": info.get("regularMarketChangePercent"),
        }
    except Exception as e:
        return {"error": str(e)}


def parse_signal(text: str, decision: str):
    """Extract trading signals from analysis text."""
    if not text:
        text = ""
    text_lower = text.lower()
    decision_upper = decision.upper() if decision else ""

    signal = {
        "action": "",
        "entry": "",
        "stop_loss": "",
        "take_profit": "",
        "reason": "",
        "confidence": "",
    }

    if "BUY" in decision_upper:
        signal["action"] = "BUY"
    elif "SELL" in decision_upper:
        signal["action"] = "SELL"
    elif "HOLD" in decision_upper:
        signal["action"] = "HOLD"

    patterns = {
        "entry": [r"(?:entry|enter|buy)(?:\s+at|:|\s+price)?\s*:?\s*\$?([\d,]+\.?\d*)",
                   r"(?:price|level)(?:\s+at|:)?\s*:?\s*\$?([\d,]+\.?\d*)",
                   r"target\s+price\s*:?\s*\$?([\d,]+\.?\d*)"],
        "stop_loss": [r"(?:stop\s*(?:loss|level)|sl)\s*:?\s*\$?([\d,]+\.?\d*)",
                       r"(?:stop|sl)\s+(?:at|:)?\s*\$?([\d,]+\.?\d*)"],
        "take_profit": [r"(?:take\s*profit|tp|target)\s*:?\s*\$?([\d,]+\.?\d*)",
                         r"(?:tp|target)\s+(?:at|:)?\s*\$?([\d,]+\.?\d*)"],
    }
    for key, pats in patterns.items():
        for p in pats:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                val = m.group(1).replace(",", "")
                try:
                    signal[key] = f"${float(val):.2f}" if float(val) < 100000 else f"${float(val):,.0f}"
                except ValueError:
                    signal[key] = m.group(1)
                break

    conf_patterns = [
        (r"(?:confidence|conviction)\s*:?\s*(\d+)%?", "confidence"),
        (r"(?:high|strong|very confident)", lambda: "High"),
        (r"(?:medium|moderate)", lambda: "Medium"),
        (r"(?:low|weak|not confident)", lambda: "Low"),
    ]
    for pat, key in conf_patterns:
        if callable(key):
            if re.search(pat, text, re.IGNORECASE):
                signal["confidence"] = key()
                break
        else:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                signal["confidence"] = f"{m.group(1)}%"
                break

    reason_patterns = [
        r"(?:rationale|reason|because|due to|basis)(?::|is)\s*(.*?)(?:\.\s*(?:FINAL|Recommendation|$))",
        r"(?:recommend|suggest)\s*(?:buying|selling|a\s+)?(?:because|as|since)\s*(.*?)(?:\.\s*(?:FINAL|$))",
    ]
    for pat in reason_patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            signal["reason"] = m.group(1).strip()[:300]
            break
    if not signal["reason"] and len(text) > 50:
        signal["reason"] = text[:200].strip() + "..."

    return signal


def run_analysis(ticker: str, date: str):
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "deepseek"
    config["deep_think_llm"] = "nvidia/deepseek-v4-flash"
    config["quick_think_llm"] = "nvidia/deepseek-v4-flash"
    config["backend_url"] = "https://blockrun.ai/api/v1"
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["output_language"] = "English"
    os.environ["DEEPSEEK_API_KEY"] = "not-needed"
    os.environ["OPENAI_API_KEY"] = "not-needed"

    ta = TradingAgentsGraph(debug=False, config=config)
    final_state, decision = ta.propagate(ticker, date)

    result = {
        "ticker": ticker,
        "date": date,
        "decision": str(decision) if decision else "N/A",
    }

    analysts = {
        "market_report": ("Market Analyst", "chart-bar"),
        "sentiment_report": ("Sentiment Analyst", "chat"),
        "news_report": ("News Analyst", "newspaper"),
        "fundamentals_report": ("Fundamentals Analyst", "chart-line"),
    }
    for key, (label, icon) in analysts.items():
        content = final_state.get(key)
        if content:
            result[key] = {"label": label, "content": content, "icon": icon}

    debate = final_state.get("investment_debate_state")
    if debate:
        parts = []
        if debate.get("bull_history"):
            parts.append({"type": "Bull Researcher", "content": debate["bull_history"]})
        if debate.get("bear_history"):
            parts.append({"type": "Bear Researcher", "content": debate["bear_history"]})
        if debate.get("judge_decision"):
            parts.append({"type": "Research Manager", "content": debate["judge_decision"]})
        if parts:
            result["research"] = parts

    trader_text = final_state.get("trader_investment_plan")
    if trader_text:
        result["trader"] = trader_text

    risk = final_state.get("risk_debate_state")
    if risk:
        risk_list = []
        if risk.get("aggressive_history"):
            risk_list.append({"type": "Aggressive Analyst", "content": risk["aggressive_history"]})
        if risk.get("conservative_history"):
            risk_list.append({"type": "Conservative Analyst", "content": risk["conservative_history"]})
        if risk.get("neutral_history"):
            risk_list.append({"type": "Neutral Analyst", "content": risk["neutral_history"]})
        if risk.get("judge_decision"):
            risk_list.append({"type": "Portfolio Manager", "content": risk["judge_decision"]})
        if risk_list:
            result["risk"] = risk_list

    combined_text = f"{trader_text or ''}\n{decision or ''}"
    result["signal"] = parse_signal(combined_text, str(decision))

    return result


@app.get("/", response_class=HTMLResponse)
async def index():
    return tpl.get_template("index.html").render()


@app.get("/api/stocks")
async def stocks():
    return JSONResponse(POPULAR_STOCKS)


@app.get("/api/price")
async def price(ticker: str = Query("AAPL"), period: str = Query("1y")):
    data = get_stock_history(ticker, period)
    if data is None:
        return JSONResponse({"error": f"No data for {ticker}"}, status_code=404)
    return JSONResponse(data)


@app.get("/api/analyze")
async def analyze(ticker: str = Query("AAPL"), date: str = Query(None)):
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, run_analysis, ticker, date)
    return JSONResponse(result)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
