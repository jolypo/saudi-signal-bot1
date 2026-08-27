from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # =========================================================
    # TELEGRAM
    # =========================================================

    signal_bot_name: str = "TASI_KSA_signal_bot"
    signal_bot_token: str

    profit_bot_name: str = "TASI_KSA_profit11_bot"
    profit_bot_token: str

    loss_bot_name: str = "TASI_KSA_loss1122_bot"
    loss_bot_token: str

    report_bot_name: str = "TASI_KSA_report112233_bot"
    report_bot_token: str

    # Telegram Group
    telegram_chat_id: int

    # Telegram Channel
    telegram_channel_id: int

    # Private admin account
    telegram_admin_user_id: int

    # auto -> webhook on Render / polling locally
    telegram_mode: str = "auto"

    # =========================================================
    # DATA PROVIDERS
    # =========================================================

    # Start the day with SAHMK.
    # When its safe daily limit is reached,
    # automatically switch to Tasilab.
    data_provider_primary: str = "sahmk"

    # =========================================================
    # SAHMK
    # =========================================================

    sahmk_api_key: str
    sahmk_base_url: str = "https://api.sahmk.sa/api/v1"
    sahmk_plan: str = "free"

    # Protect against minute-rate limits
    sahmk_min_request_interval: float = 6.5

    # Official/free daily allowance used by the project
    sahmk_local_daily_limit: int = 100

    # Switch to Tasilab before reaching the absolute daily limit
    sahmk_daily_switch_limit: int = 90

    # If SAHMK returns HTTP 429,
    # temporarily use Tasilab instead of retrying repeatedly.
    sahmk_cooldown_on_429_seconds: int = 120

    # =========================================================
    # TASILAB
    # =========================================================

    tasilab_api_key: str

    # Keep configurable in Render in case their API URL changes.
    tasilab_base_url: str = "https://api.tasilab.com"

    # Request timeout
    tasilab_timeout_seconds: float = 15.0

    # Local safety interval between Tasilab requests.
    # Tasilab has a much larger allowance, but we still avoid bursts.
    tasilab_min_request_interval: float = 0.6
    # Smaller bulk chunks reduce upstream/Cloudflare pressure.
    tasilab_bulk_chunk_size: int = 20

    # If bulk quotes are unavailable with 5xx, scan a bounded number of
    # symbols through the documented single-quote endpoint.
    tasilab_single_fallback_scan_limit: int = 60

    # Temporarily stop using the bulk endpoint after consecutive 5xx errors.
    tasilab_bulk_cooldown_seconds: int = 300

    # Open a provider-wide circuit only if single quotes themselves fail
    # repeatedly. A broken bulk endpoint alone must not disable Tasilab.
    tasilab_circuit_failure_threshold: int = 3
    tasilab_circuit_cooldown_seconds: int = 300

    # =========================================================
    # PROVIDER ROUTER
    # =========================================================

    # If SAHMK has reached the safe daily limit:
    # SAHMK -> Tasilab
    provider_switch_on_daily_limit: bool = True

    # Legacy compatibility flag. Temporary SAHMK 429 never switches provider.
    # Tasilab is reserved for the daily quota switch only.
    provider_switch_on_429: bool = False

    # If the active provider fails:
    # allow the other provider to serve the request.
    provider_fallback_enabled: bool = True

    # =========================================================
    # HISTORICAL DATA
    # =========================================================

    # Keep Yahoo for historical analysis for now.
    historical_provider: str = "yahoo"

    historical_max_price_gap_pct: float = 15.0
    # Entry-driving intraday research must track the live/delayed quote much more closely.
    intraday_max_price_gap_pct: float = 2.5
    historical_intraday_max_age_minutes: int = 30

    intraday_min_bars: int = 60
    swing_min_bars: int = 120

    # =========================================================
    # STORAGE / HEALTH
    # =========================================================

    state_dir: str = "data"
    health_interval: int = 600

    # =========================================================
    # SCHEDULER / TRADE MONITOR
    # =========================================================

    # Internal monitoring every 15 minutes (matches delayed-data economics).
    # Scheduler NEVER creates new signals.
    scan_interval_seconds: int = 900

    # Monitor up to 3 open trades per cycle
    trade_monitor_quotes_per_cycle: int = 3

    # Public price update every 20 minutes
    trade_price_update_minutes: int = 20

    # =========================================================
    # MANUAL SIGNAL SCAN
    # =========================================================

    manual_quotes_per_signal: int = 50
    detail_quotes_per_signal: int = 5

    # A discovered setup is previewed privately and must be confirmed quickly.
    # Confirmation reuses the same scan result and makes no additional market API call.
    signal_confirmation_expiry_minutes: int = 5

    # =========================================================
    # CACHE
    # =========================================================

    market_cache_seconds: int = 600
    universe_refresh_seconds: int = 21600
    bootstrap_universe_file: str = "app/data/tasi_universe.json"

    # =========================================================
    # SAUDI MARKET HOURS
    # =========================================================

    market_open: str = "10:00"
    market_close: str = "15:00"

    # Saudi anti-fake-momentum entry window. The first 30 minutes are
    # observation-only; new entries stop before the closing auction.
    signal_window_start: str = "10:30"
    signal_window_end: str = "14:30"

    timezone: str = "Asia/Riyadh"

    allow_off_hours_scan: bool = False

    # =========================================================
    # SIGNAL QUALITY
    # =========================================================

    min_score: float = 82
    min_probability: float = 65

    # Saudi-market liquidity gate. Missing/zero traded value is treated as
    # unverified liquidity and rejected for new signals.
    min_daily_traded_value: float = 2_000_000

    max_daily_signals: int = 3
    max_open_trades: int = 5

    # =========================================================
    # RISK MANAGEMENT
    # =========================================================

    max_risk_per_trade: float = 0.01

    data_max_delay_minutes: int = 30

    min_rr: float = 1.8

    tp1_percent: float = 30
    tp2_percent: float = 30
    tp3_percent: float = 40

    slippage_bps: float = 5
    # Saudi Exchange total trading commission baseline; keep configurable for broker-specific costs.
    fee_bps: float = 15.5

    allow_long: bool = True

    # Paper Trading only
    paper_mode: bool = True

    # =========================================================
    # TRAILING STOP
    # =========================================================

    trailing_stop_enabled: bool = False

    trailing_after_tp1_to_entry: bool = True
    trailing_after_tp2_atr: float = 1.0

    # =========================================================
    # PROFIT / LOSS ALERTS
    # =========================================================

    profit_alert_thresholds: str = "2,5,10,15,20"

    near_sl_warning_pct: float = 0.5

    # =========================================================
    # REPORTS / TELEGRAM MEDIA
    # =========================================================

    # Static approved visual assets bundled with the project.
    trade_card_image: str = "app/assets/telegram/trade_card.png"
    profit_update_image: str = "app/assets/telegram/profit_update.png"
    daily_report_image: str = "app/assets/telegram/daily_report.png"
    weekly_report_image: str = "app/assets/telegram/weekly_report.png"

    daily_report_enabled: bool = True
    daily_report_hour: int = 15
    daily_report_minute: int = 5

    weekly_report_enabled: bool = True

    # Thursday
    weekly_report_weekday: int = 3

    weekly_report_hour: int = 15
    weekly_report_minute: int = 5

    # =========================================================
    # PYDANTIC
    # =========================================================

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
