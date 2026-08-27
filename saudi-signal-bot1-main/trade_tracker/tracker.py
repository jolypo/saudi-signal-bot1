import json
from datetime import datetime, timezone
from database.models import Trade

class TradeTracker:
    def __init__(self, Session): self.Session = Session

    def open(self, sig, regime='NEUTRAL', sector='غير محدد'):
        s = self.Session()
        t = Trade(symbol=sig.symbol, name=sig.name, entry=(sig.entry_low+sig.entry_high)/2,
                  entry_time=datetime.now(timezone.utc), tp1=sig.tp1, tp2=sig.tp2, tp3=sig.tp3,
                  sl=sig.sl, probability=sig.probability, score=sig.score,
                  strategy='TREND_BREAKOUT_ATR', market_regime=regime, sector=sector,
                  status='OPEN', paper=True, reasons=json.dumps(sig.reasons, ensure_ascii=False), notified_levels='')
        s.add(t); s.commit(); s.refresh(t); s.close(); return t

    def open_trades(self):
        s=self.Session(); rows=s.query(Trade).filter(Trade.status=='OPEN').all(); s.expunge_all(); s.close(); return rows

    def update(self, t, current, profit_levels=None, trailing_enabled=True):
        profit_levels = profit_levels or []
        s=self.Session(); row=s.get(Trade,t.id)
        if not row: s.close(); return []
        pnl=(current-row.entry)/row.entry*100
        row.max_profit=max(row.max_profit or 0,pnl)
        row.max_drawdown=min(row.max_drawdown or 0,pnl)
        notified=set(filter(None,(row.notified_levels or '').split(',')))
        events=[]
        for level in profit_levels:
            key=f'P{level:g}'
            if pnl >= level and key not in notified:
                notified.add(key); events.append((f'PROFIT_{level:g}', pnl))
        if current >= row.tp3 and 'TP3' not in notified:
            row.exit=current; row.exit_time=datetime.now(timezone.utc); row.result=pnl; row.status='CLOSED'; notified.add('TP3'); events.append(('TP3', pnl))
        elif current >= row.tp2 and 'TP2' not in notified:
            notified.add('TP2'); events.append(('TP2', pnl))
            if trailing_enabled: row.sl=max(row.sl,row.entry)
        elif current >= row.tp1 and 'TP1' not in notified:
            notified.add('TP1'); events.append(('TP1', pnl))
            if trailing_enabled: row.sl=max(row.sl,row.entry)
        elif current <= row.sl and 'SL' not in notified:
            row.exit=current; row.exit_time=datetime.now(timezone.utc); row.result=pnl; row.status='CLOSED'; notified.add('SL'); events.append(('SL', pnl))
        row.notified_levels=','.join(sorted(notified))
        s.commit(); s.close(); return events
