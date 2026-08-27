# Project Specification

## Pipeline — SAHMK Free mode

Manual `/signal` -> market-hours guard -> TASI active-stock ranking (`/market/volume/`) -> screen up to 50 -> detailed single quotes for up to 5 finalists -> quote-only momentum/risk checks -> Signal Bot -> Paper Trade -> Profit/Loss tracking -> weekly report.

No automatic signal discovery. Scheduler only monitors open paper trades and scheduled messages while the service is running.

## Signal fields

Symbol, Arabic/English name, direction, entry zone, SL, TP1/TP2/TP3, R/R, screening score, empirical probability status/samples, strategy, market regime, sector, discovery time, expected TP windows.

## Probability

Probability is never invented. Before enough closed paper trades exist in the same bucket, the signal is marked `UNVALIDATED` and shows the real sample count. This does not block initial Paper Trades, because those trades are needed to build the empirical sample. After 30 outcomes in the bucket, probability becomes `VALIDATED` and `MIN_PROBABILITY` is enforced.

## Data provider

SAHMK is isolated behind `DataProvider`.

Free mode deliberately avoids:
- `/quotes/` bulk endpoint (Starter+)
- `/historical/{symbol}/` (Starter+)

Free mode uses:
- `/market/volume/`
- `/quote/{symbol}/`
- `/market/summary/`
- `/companies/`

## Paper Trading

No broker/execution APIs are present.

## Saudi Signal Quality Policy v2

The engine is intentionally selective. `/signal` may legitimately return no trade. It must never create a trade simply because several correlated price indicators (EMA/RSI/MACD/Momentum) agree. Participation, structure and entry quality are mandatory.

Accepted paper signals: grade A or A+ only, score >= 82, R/R >= 1.8, no hard reject.
Default entry window: 10:30–14:30 Asia/Riyadh.
Primary use case with delayed feed: short swing, typically 2–5 sessions.


## Saudi MTF Anti-Fake v3 — final review additions

- Live entry decision requires 15m + 60m + daily agreement.
- Intraday VWAP is session-based when datetime data is available.
- Intraday RVOL is adjusted against the same Riyadh time slot across prior sessions when enough data exists.
- No live signal when market context is unavailable or TASI is bearish.
- Hard rejects include failed breakout, nearby resistance, volume climax/distribution, price-volume divergence, excessive VWAP/EMA extension and weak ADX/DI control.
- Saudi tick-size rounding is mandatory for trade levels.
- Paper trade TP allocations are realized 30/30/40 and net results include estimated fees/slippage.
- SAHMK 403/5xx may use a one-call Tasilab fallback without falsely marking SAHMK daily quota exhausted.
- `render.yaml` deployment defaults match the strategy defaults.


## Private Telegram Control Menu

The private admin chat uses a persistent Arabic reply keyboard for the main controls (signal scan, market, open trades, performance, report, health, settings, risk, Tasilab diagnostic, pause/resume). Legacy slash commands remain supported as a fallback. Group/channel chats remain publish-only.

## Private approval gate
All new trade opportunities discovered from the private `🔎 فحص فرصة` control must pass through a private approval gate before publication. The operator chooses `✅ إرسال الصفقة` or `❌ إلغاء الصفقة`. Approval is short-lived and does not repeat the market scan/API calls.
