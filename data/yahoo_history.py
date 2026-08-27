"""Optional free historical adapter with explicit audit logging."""
from __future__ import annotations
import time
import pandas as pd
from diagnostics import caller_chain, event

class YahooHistory:
    def __init__(self, period="60d", interval="15m"):
        self.period = period
        self.interval = interval
        event("yahoo_client_init", period=period, interval=interval)

    def history(self, symbol: str) -> pd.DataFrame:
        event(
            "yahoo_http_outbound",
            symbol=symbol,
            period=self.period,
            interval=self.interval,
            callers=caller_chain(),
        )
        started = time.monotonic()
        try:
            import yfinance as yf
            df = yf.download(
                f"{symbol}.SR", period=self.period, interval=self.interval,
                auto_adjust=False, progress=False, threads=False
            )
            event(
                "yahoo_http_complete",
                symbol=symbol,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                rows=0 if df is None else len(df),
            )
        except Exception as exc:
            event(
                "yahoo_http_error",
                symbol=symbol,
                error=type(exc).__name__,
                message=str(exc)[:240],
            )
            raise
        if df is None or df.empty:
            return pd.DataFrame()
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df.columns = [str(c).lower() for c in df.columns]
        needed = ["open","high","low","close","volume"]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            return pd.DataFrame()
        return df[needed].dropna().copy()
