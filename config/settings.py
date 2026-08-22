from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    telegram_bot_token: str
    sahmk_api_key: str
    sahmk_base_url: str = 'https://api.sahmk.sa/api/v1'
    database_url: str = 'sqlite:///saudi_signals.db'
    bot_public: bool = True
    data_mode: str = 'delayed'
    poll_seconds: int = 1800
    trade_monitor_seconds: int = 900
    port: int = 10000
    history_period: str = '60d'
    history_interval: str = '15m'
    paper_mode: bool = True
    scan_top_n: int = 25
    min_score: float = 72
    min_probability: float = 65
    max_new_signals_per_day: int = 5
    duplicate_cooldown_min: int = 180
    min_rr: float = 1.8
    trailing_stop_enabled: bool = True
    notify_profit_levels: str = '2,5,10,15,20'
    timezone: str = 'Asia/Riyadh'
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    @property
    def profit_levels(self): return [float(x) for x in self.notify_profit_levels.split(',') if x.strip()]

settings = Settings()
