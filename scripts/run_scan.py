from pathlib import Path
from config.settings import settings
from data.sahmk import SahmkClient
from data.provider import SAHMKProvider
from data.history_loader import load_symbol
from signal_engine.engine import build_signal
from charts.signal_card import make_signal_chart

provider=SAHMKProvider(SahmkClient(settings.sahmk_base_url,settings.sahmk_api_key))
snap=provider.market_snapshot(); bullish=snap['summary'].get('market_mood','').lower()=='bullish'
for c in provider.candidate_symbols(settings.scan_top_n):
    symbol=c['symbol']; df=load_symbol(symbol)
    if df is None: continue
    sig=build_signal(symbol,c.get('name',''),df,bullish,settings.min_rr)
    if sig and sig.score>=settings.min_score and sig.probability>=settings.min_probability:
        print(sig); print(make_signal_chart(sig))
