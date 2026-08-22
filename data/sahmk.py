import requests
from typing import Any

class SahmkClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 20):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'X-API-Key': api_key, 'Accept': 'application/json'})
        self.timeout = timeout
    def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        r = self.session.get(f'{self.base_url}/{path.lstrip("/")}', params=params, timeout=self.timeout)
        r.raise_for_status(); return r.json()
    def companies(self, limit=500, offset=0):
        return self.get('/companies/', {'market':'TASI','limit':limit,'offset':offset})
    def quote(self, symbol: str):
        return self.get(f'/quote/{symbol}/', {'data_mode':'delayed'})
    def market_summary(self): return self.get('/market/summary/', {'index':'TASI','data_mode':'delayed'})
    def sectors(self): return self.get('/market/sectors/', {'index':'TASI','data_mode':'delayed'})
    def gainers(self, limit=20): return self.get('/market/gainers/', {'index':'TASI','limit':limit,'data_mode':'delayed'})
    def losers(self, limit=20): return self.get('/market/losers/', {'index':'TASI','limit':limit,'data_mode':'delayed'})
    def volume(self, limit=20): return self.get('/market/volume/', {'index':'TASI','limit':limit,'data_mode':'delayed'})
    def value(self, limit=20): return self.get('/market/value/', {'index':'TASI','limit':limit,'data_mode':'delayed'})
