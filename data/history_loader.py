from pathlib import Path
import pandas as pd

def load_symbol(symbol, root='data/history'):
    p=Path(root)/f'{symbol}.csv'
    if not p.exists(): return None
    df=pd.read_csv(p); df.columns=[c.lower() for c in df.columns]
    if 'date' not in df: raise ValueError(f'{p} requires date column')
    df['date']=pd.to_datetime(df['date']); return df.sort_values('date').set_index('date')
