from datetime import datetime, timezone
import httpx
import pandas as pd


class YahooHistoricalProvider:
    """Free research-only historical provider for Saudi stocks via Yahoo chart data.

    Saudi Exchange symbols are mapped as 2140 -> 2140.SR. This provider is
    deliberately secondary: SAHMK remains the quote/monitoring source.
    """

    def __init__(self, timeout=20.0):
        self.client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})

    async def close(self):
        await self.client.aclose()

    @staticmethod
    def ticker(symbol):
        symbol = str(symbol).strip().upper()
        return symbol if "." in symbol else f"{symbol}.SR"

    async def _chart(self, symbol, range_, interval):
        ticker = self.ticker(symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = await self.client.get(url, params={"range": range_, "interval": interval, "includePrePost": "false", "events": "div,splits"})
        r.raise_for_status()
        payload = r.json()
        result = ((payload.get("chart") or {}).get("result") or [])
        if not result:
            return None, None
        item = result[0]
        timestamps = item.get("timestamp") or []
        quote = (((item.get("indicators") or {}).get("quote") or [{}])[0])
        if not timestamps or not quote:
            return None, None
        n = len(timestamps)
        data = {
            "timestamp": timestamps,
            "open": (quote.get("open") or [None] * n),
            "high": (quote.get("high") or [None] * n),
            "low": (quote.get("low") or [None] * n),
            "close": (quote.get("close") or [None] * n),
            "volume": (quote.get("volume") or [None] * n),
        }
        df = pd.DataFrame(data)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close", "volume"]).sort_values("datetime").reset_index(drop=True)

        # Do not let a still-forming Yahoo intraday candle create a weak RVOL,
        # fake wick, or false breakout signal. Yahoo timestamps are bar starts.
        if interval.endswith("m") and not df.empty:
            try:
                minutes = int(interval[:-1])
                last_start = df.iloc[-1]["datetime"]
                now = pd.Timestamp.now(tz="UTC")
                if last_start + pd.Timedelta(minutes=minutes) > now:
                    df = df.iloc[:-1].reset_index(drop=True)
            except (TypeError, ValueError):
                pass

        meta = item.get("meta") or {}
        return df, meta

    async def intraday(self, symbol):
        return await self._chart(symbol, "1mo", "15m")

    async def daily(self, symbol):
        return await self._chart(symbol, "1y", "1d")

    async def datasets(self, symbol):
        intraday, imeta = await self.intraday(symbol)
        daily, dmeta = await self.daily(symbol)
        return {"intraday": intraday, "daily": daily, "intraday_meta": imeta or {}, "daily_meta": dmeta or {}}

    @staticmethod
    def validate_against_quote(df, sahmk_price, max_gap_pct=15.0):
        if df is None or df.empty or sahmk_price <= 0:
            return False
        last = float(df.iloc[-1]["close"])
        gap = abs(last - float(sahmk_price)) / float(sahmk_price) * 100
        return gap <= max_gap_pct

    @staticmethod
    def last_stamp(df):
        if df is None or df.empty:
            return None
        value = df.iloc[-1]["datetime"]
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value
