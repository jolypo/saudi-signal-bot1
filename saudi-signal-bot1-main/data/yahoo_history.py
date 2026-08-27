"""Optional free historical adapter.

Uses Yahoo Finance through yfinance for research/backtesting only. It is intentionally
separate from SAHMK so the data provider can be replaced later.
"""
from __future__ import annotations
import pandas as pd

class YahooHistory:
    def __init__(self, period="60d", interval="15m"):
        self.period = period
        self.interval = interval

    def history(self, symbol: str) -> pd.DataFrame:
        import yfinance as yf
        df = yf.download(
            f"{symbol}.SR", period=self.period, interval=self.interval,
            auto_adjust=False, progress=False, threads=False
        )
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
