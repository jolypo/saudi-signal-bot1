import numpy as np
from indicators.technical import enrich

def score_stock(df, market_bullish=True):
    if len(df)<60: return None
    x=enrich(df).dropna().iloc[-1]
    score=0; reasons=[]
    if x.close>x.ema20>x.ema50: score+=18; reasons.append('اتجاه صاعد EMA')
    elif x.close<x.ema20<x.ema50: score-=18
    if x.close>x.vwap: score+=10; reasons.append('فوق VWAP')
    if 50<=x.rsi<=68: score+=10; reasons.append('RSI مناسب')
    if x.macd_hist>0: score+=10; reasons.append('MACD إيجابي')
    if x.rel_volume>=1.5: score+=15; reasons.append('حجم تداول مرتفع')
    if x.momentum_5>1: score+=10; reasons.append('Momentum')
    if x.close>=x.resistance*0.995: score+=12; reasons.append('اختبار/اختراق مقاومة')
    if market_bullish: score+=5; reasons.append('توافق مع TASI')
    else: score-=8
    return {'score':float(np.clip(score,0,100)),'reasons':reasons,'row':x}
