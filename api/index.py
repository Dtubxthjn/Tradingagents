import logging
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AnalyzeRequest(BaseModel):
    ticker: str = Field(..., description="Ticker symbol (e.g. NVDA, 0700.HK, BTC-USD)")
    date: str = Field(..., description="Analysis date in YYYY-MM-DD format")
    asset_type: str = Field(default="stock", description="Asset type: stock or crypto")
    llm_provider: Optional[str] = Field(default=None, description="LLM provider override")
    deep_think_llm: Optional[str] = Field(default=None, description="Deep thinking model override")
    quick_think_llm: Optional[str] = Field(default=None, description="Quick thinking model override")


class AnalyzeResponse(BaseModel):
    ticker: str
    date: str
    decision: str
    signal: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("TradingAgents API starting up")
    yield
    logger.info("TradingAgents API shutting down")


app = FastAPI(
    title="TradingAgents API",
    description="Multi-Agent LLM Financial Trading Framework",
    version="0.2.5",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    config = DEFAULT_CONFIG.copy()
    if req.llm_provider:
        config["llm_provider"] = req.llm_provider
    if req.deep_think_llm:
        config["deep_think_llm"] = req.deep_think_llm
    if req.quick_think_llm:
        config["quick_think_llm"] = req.quick_think_llm

    try:
        graph = TradingAgentsGraph(debug=False, config=config)
        final_state, signal = graph.propagate(
            req.ticker, req.date, asset_type=req.asset_type
        )
        decision = final_state.get("final_trade_decision", "")
        return AnalyzeResponse(
            ticker=req.ticker,
            date=req.date,
            decision=decision,
            signal=signal,
        )
    except Exception as e:
        logger.exception("Analysis failed for %s on %s", req.ticker, req.date)
        raise HTTPException(status_code=500, detail=str(e))
