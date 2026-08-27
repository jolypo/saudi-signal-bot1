from abc import ABC, abstractmethod
from .sahmk import SahmkClient

class MarketDataProvider(ABC):
    @abstractmethod
    def market_snapshot(self): ...
    @abstractmethod
    def candidate_symbols(self, limit: int): ...
    @abstractmethod
    def quote(self, symbol: str): ...

class SAHMKProvider(MarketDataProvider):
    def __init__(self, client: SahmkClient): self.client=client
    def market_snapshot(self):
        return {'summary':self.client.market_summary(),'sectors':self.client.sectors(),
                'gainers':self.client.gainers(20),'losers':self.client.losers(20),
                'volume':self.client.volume(20),'value':self.client.value(20)}
    def candidate_symbols(self, limit=25):
        # Free tier has no bulk quote endpoint. Combine free market leader lists and deduplicate.
        snap=self.market_snapshot(); items=[]
        for k in ('gainers','volume','value'):
            for x in snap[k].get('stocks', snap[k].get(k, [])):
                if x.get('symbol') and x['symbol'] not in {i['symbol'] for i in items}: items.append(x)
        return items[:limit]
    def quote(self,symbol): return self.client.quote(symbol)
