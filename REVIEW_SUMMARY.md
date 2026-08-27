# Final Expert Review — Saudi TASI Signal System

## Verdict
Production-ready for **paper trading / signal validation**, with deliberately conservative Saudi-market entry logic. It does not claim guaranteed profitability.

## Trading logic reviewed and corrected
- Saudi trading window: new signals only 10:30–14:30 KSA; monitoring remains separate.
- Live strategy is short swing (2–5 sessions), not true day trading.
- Mandatory MTF confirmation: 15m entry + completed 60m confirmation + daily context.
- Corrected 60m aggregation: 15m bar-start timestamps are grouped left-closed and partial hours are dropped.
- Session VWAP for intraday; rolling VWAP only as fallback/daily context.
- Time-of-day RVOL when enough intraday history exists.
- Anti-fake gates: failed breakout, upper wick, volume climax, price-volume divergence, VWAP/EMA extension, resistance proximity, DI/ADX weakness, bearish/unknown TASI context.
- Signal grades: only A / A+ can create a paper trade; hard rejects override score.
- Score remains a technical quality score, not a win probability.
- Probability remains UNVALIDATED until at least 30 closed outcomes exist in the same empirical bucket.
- Saudi tick-size rounding is applied to entries, stop and targets.
- Short-swing stop logic is separated from day-trade stop logic to avoid overly tight multi-session stops.
- Minimum R:R = 1.8.
- Live traded-value gate = SAR 2,000,000 minimum before a new signal can be created.

## Engineering / quota corrections
- Telegram signal discovery remains manual only.
- Scheduler never creates signals.
- Trade monitoring changed from every 5 minutes to every 15 minutes because source data is delayed and SAHMK free quota is limited.
- Monitor batch increased to 3 trades per cycle, balancing freshness and daily request budget.
- TradingService boots from the router's bundled/runtime universe without calling SAHMK company pagination on every Render restart.
- Provider fallback behavior retained: SAHMK primary; Tasilab fallback for supported failures/daily limit.
- Freshness checks reject missing timestamps, future timestamps and stale data.
- Intraday Yahoo research data max age tightened to 30 minutes and max live-price gap tightened to 2.5%.

## Known limitations intentionally not faked
- Sector relative-strength scoring is not awarded unless reliable live sector data is available.
- Time-adjusted RVOL falls back safely when sufficient same-time historical bars are unavailable.
- Yahoo remains research/historical confirmation, not the live execution-price authority.
- Paper state on Render can still be ephemeral unless persistent storage is configured.

## Validation
- pytest: 35 passed
- Python compile: passed
- Secret-pattern scan: passed
- No .env included in deliverable


## Private Telegram Control Menu

The private admin chat uses a persistent Arabic reply keyboard for the main controls (signal scan, market, open trades, performance, report, health, settings, risk, Tasilab diagnostic, pause/resume). Legacy slash commands remain supported as a fallback. Group/channel chats remain publish-only.

## Telegram visual/report review
- Added approved static assets for trade, profit-update, daily-report, and weekly-report presentation.
- Profit/TP notifications persist and use original signal message IDs for Telegram reply threading.
- Added safe non-reply fallback for destinations that do not permit reply threading.
- Added scheduled daily report alongside the existing weekly report.
- Report delivery order is image first, then text.
- Added private-only, zero-market-API display tests for trade/profit/daily/weekly outputs.
- Regression suite after changes: 40 passed.

## Final confirmation-flow review
- New setups are staged privately and are not registered or published before admin approval.
- Confirmation reuses stored scan data and makes zero additional market-data API calls.
- Cancel does not count as a daily signal and does not create an open Paper Trade.
- Pending setup expires after 5 minutes by default to reduce stale-entry risk.
- Telegram publication failure to all configured destinations rolls back the Paper Trade.
- The approved existing visual assets are reused; no new image assets were generated in this revision.
