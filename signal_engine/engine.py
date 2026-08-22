from dataclasses import dataclass
from scanner.scorer import score_stock
from probability.calibration import empirical_probability

@dataclass
class Signal:
    symbol:str; name:str; side:str; entry_low:float; entry_high:float; sl:float
    tp1:float; tp2:float; tp3:float; score:float; probability:float; rr:float; reasons:list

def build_signal(symbol,name,df,market_bullish=True,min_rr=1.8):
    result=score_stock(df,market_bullish)
    if not result: return None
    r=result['row']; entry=float(r.close); atr=float(r.atr)
    # Structural/volatility stop: below recent support and ATR-based distance.
    sl=min(entry-1.2*atr,float(r.support)*0.998)
    if sl<=0 or sl>=entry: return None
    risk=entry-sl
    tp1=entry+1.5*risk; tp2=entry+2.2*risk; tp3=entry+3.2*risk
    rr=(tp2-entry)/risk
    if rr<min_rr: return None
    probability=empirical_probability(df, tp_r=1.5, sl_r=max(1.0, risk/atr if atr else 1.0))
    # Never invent a probability. No sufficient historical sample => no signal.
    if probability is None: return None
    return Signal(symbol,name,'شراء',entry*0.998,entry*1.002,sl,tp1,tp2,tp3,
                  result['score'],probability,rr,result['reasons'])
