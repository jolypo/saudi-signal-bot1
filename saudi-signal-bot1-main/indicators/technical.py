import numpy as np
import pandas as pd

def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    rs=up.ewm(alpha=1/n,adjust=False).mean()/dn.ewm(alpha=1/n,adjust=False).mean().replace(0,np.nan)
    return 100-(100/(1+rs))
def atr(df,n=14):
    pc=df.close.shift(1); tr=pd.concat([(df.high-df.low),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()
def macd(s,fast=12,slow=26,signal=9):
    m=ema(s,fast)-ema(s,slow); sig=ema(m,signal); return m,sig,m-sig
def vwap(df):
    tp=(df.high+df.low+df.close)/3
    # Anchor VWAP by trading day when the index is datetime-like.
    if hasattr(df.index, 'date'):
        key = df.index.date
        pv=(tp*df.volume).groupby(key).cumsum()
        vv=df.volume.groupby(key).cumsum().replace(0,np.nan)
        return pv/vv
    return (tp*df.volume).cumsum()/df.volume.cumsum().replace(0,np.nan)
def enrich(df):
    x=df.copy()
    for n in (9,20,50,200): x[f'ema{n}']=ema(x.close,n)
    x['rsi']=rsi(x.close); x['atr']=atr(x); x['volume_avg']=x.volume.rolling(20).mean(); x['vwap']=vwap(x)
    x['macd'],x['macd_signal'],x['macd_hist']=macd(x.close)
    x['support']=x.low.rolling(20).min(); x['resistance']=x.high.rolling(20).max()
    x['rel_volume']=x.volume/x.volume_avg.replace(0,np.nan)
    x['momentum_5']=x.close.pct_change(5)*100
    return x
