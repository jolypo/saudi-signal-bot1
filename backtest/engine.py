import pandas as pd
from indicators.technical import enrich

def backtest(df, strategy_fn, train_ratio=.6, validation_ratio=.2):
    df=df.sort_index(); n=len(df); train=df.iloc[:int(n*train_ratio)]; val=df.iloc[int(n*train_ratio):int(n*(train_ratio+validation_ratio))]; test=df.iloc[int(n*(train_ratio+validation_ratio)):]
    return {'train':len(train),'validation':len(val),'out_of_sample':len(test),'note':'Strategy execution should be run separately on each split; never fit calibration on OOS.'}

def load_csv(path):
    df=pd.read_csv(path); df.columns=[c.lower() for c in df.columns]; df['date']=pd.to_datetime(df['date']); return df.set_index('date').sort_index()
