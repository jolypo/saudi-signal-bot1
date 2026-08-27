# TASI KSA Professional Paper Signal Bot

نظام إشارات ورقية للسوق السعودي يعمل على Render وTelegram. إنشاء الإشارات يدوي عبر `/signal` فقط، بينما الـScheduler مخصص لمراقبة الصفقات والتقارير.

## مزودات البيانات

- **SAHMK** هو المزود الأساسي لبيانات السوق السعودي.
- الباقة المجانية في SAHMK محدودة يوميًا وبحد اندفاع لكل دقيقة.
- المشروع يباعد بين طلبات SAHMK لتقليل 429.
- **429 المؤقت لا يحول إلى Tasilab** ولا يجعل أمر Telegram ينتظر عدة دقائق؛ يسجل وقت `Retry-After` ويرفض الطلب مؤقتًا بسرعة.
- عندما يصل استهلاك SAHMK إلى `SAHMK_DAILY_SWITCH_LIMIT` (الافتراضي 90)، يتحول النظام إلى **Tasilab** لبقية اليوم السعودي.
- إذا أوضح SAHMK صراحة أن 429 سببه نفاد الحصة اليومية، يتم التحويل إلى Tasilab مباشرة.
- مع بداية يوم جديد بتوقيت `Asia/Riyadh` يعود SAHMK ليكون المزود الأساسي.
- **Yahoo** يبقى مصدر البيانات التاريخية للتحليل.

## Tasilab

`/v1/market/quotes` يتطلب `symbols`. لذلك يخزن المشروع قائمة رموز TASI في `data/universe_cache.json` ويستخدمها عند التحول اليومي إلى Tasilab، ثم يرتب الأسعار حسب حجم التداول محليًا.


## تحمل أخطاء Render وSAHMK

- Render في الوضع الافتراضي يستخدم نطاقات Outbound IP مشتركة. لذلك قد يرجع SAHMK حد IP يومي حتى لو كانت لوحة حسابك تعرض استهلاكًا أقل.
- المشروع يميّز بين 429 المؤقت و429 اليومي/IP اليومي من نص استجابة SAHMK و`Retry-After`.
- 429 المؤقت لا يشغّل Tasilab؛ يبقى SAHMK هو المزود الأساسي ويظهر وقت الانتظار في السجل.
- 429 اليومي أو IP اليومي يحول إلى Tasilab لبقية اليوم السعودي.
- يحتوي المشروع على `app/data/tasi_universe.json` كقائمة احتياطية مدمجة للأسهم الرئيسية. لذلك لا يعتمد Tasilab على `data/universe_cache.json` المؤقت بعد إعادة تشغيل أو Deploy جديد.
- عندما يعود SAHMK متاحًا، يتم تحديث Universe من بياناته وحفظ نسخة تشغيلية في `data/universe_cache.json`.
- إذا تغير أو شُطب رمز مستقبلًا وأعاد Tasilab خطأ تحقق للدفعة، يقسم المزود الدفعة ويعزل الرمز غير الصالح بدل إسقاط فحص السوق كله.

> ملاحظة: ملفات `data/state.json` و`data/trade_history.json` على Render Free قد تضيع عند إعادة النشر لأن نظام الملفات غير دائم. هذا لا يؤثر في بدء البوت أو Universe الاحتياطي، لكنه قد يؤثر في سجل Paper Trading المفتوح عبر عمليات إعادة النشر.

## Telegram

- الخاص مع Signal Bot: أوامر الإدارة.
- المجموعة والقناة: إشارات ونتائج وتحديثات عامة حسب إعدادات البوت.
- `/signal` يدوي فقط؛ لا يوجد اكتشاف تلقائي لصفقات جديدة.

الأوامر المدعومة حسب `bots.py`:

`/start` `/help` `/signal` `/market` `/open` `/performance` `/report` `/status` `/health` `/test_tasilab` `/settings` `/risk` `/pause` `/resume` `/myid`

## التشغيل على Render

1. ارفع المشروع إلى GitHub.
2. استخدم `render.yaml` أو Web Service باستخدام Dockerfile.
3. أضف الأسرار في Render Environment فقط.
4. لا ترفع `.env` الحقيقي إلى GitHub.
5. استخدم `.env.example` كمرجع للأسماء المطلوبة.

أهم المتغيرات:

```env
SAHMK_API_KEY=...
TASILAB_API_KEY=...
SAHMK_LOCAL_DAILY_LIMIT=100
SAHMK_DAILY_SWITCH_LIMIT=90
SAHMK_MIN_REQUEST_INTERVAL=6.5
PROVIDER_SWITCH_ON_DAILY_LIMIT=true
PROVIDER_SWITCH_ON_429=false

SIGNAL_BOT_TOKEN=...
PROFIT_BOT_TOKEN=...
LOSS_BOT_TOKEN=...
REPORT_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_CHANNEL_ID=...
TELEGRAM_ADMIN_USER_ID=...
TELEGRAM_MODE=auto
```

## فحص المشروع

```bash
python -m compileall -q app tests
pytest -q
```

تمت إضافة اختبارات لمسار مزودي البيانات والتعافي من أخطاء Render للتأكد من:

- 429 مؤقت في SAHMK لا يحول إلى Tasilab.
- بلوغ حد SAHMK اليومي يحول إلى Tasilab.
- 429 يومي صريح يحول نفس الطلب إلى Tasilab.
- تخزين Universe وتمريره إلى Tasilab.
- عدم استدعاء Tasilab `/v1/market/quotes` بدون `symbols`.

- Fresh Deploy مع SAHMK IP-daily 429 يستمر باستخدام Universe المدمج وTasilab.
- Tasilab يقسم Universe إلى دفعات صغيرة ولا يرسل `/v1/market/quotes` بدون `symbols`.
- رمز قديم/غير صالح داخل Universe لا يسقط الدفعة كلها.
- 429 من Tasilab يوقف بقية دفعات الفحص بدل تكرار الطلبات بلا داعٍ.

> النظام Paper Trading فقط ولا ينفذ صفقات حقيقية.

## Strategy engine — Saudi MTF Anti-Fake v3

Live signals now require a 15-minute entry setup, a resampled 60-minute trend confirmation, and supportive daily context. The engine uses EMA 9/20/50/200, **session VWAP** for intraday decisions, ADX14 (+DI/-DI), Wilder RSI14, MACD 12/26/9 acceleration, 5-bar momentum, Wilder ATR14, standard RVOL plus **time-of-day adjusted RVOL**, traded-value/volume trend, OBV, Accumulation/Distribution, prior structure, failed-breakout detection, wick/body quality and ATR-normalized extension.

Hard rejects override the score. Missing TASI context, bearish TASI, weak participation, 60-minute disagreement, falling daily context, failed breakouts, buying directly below resistance, price/volume divergence, excessive extension, weak DI control or distribution-style volume climax can all return **NO TRADE** even when EMA/MACD/RSI are positive.

`score` is a rules-based quality score, not a win probability. Probability becomes `VALIDATED` only after enough closed paper-trade samples in the same strategy/regime/score/RR bucket.

Paper-trade accounting follows TP1/TP2/TP3 allocations (30/30/40 by default), moves the effective stop to break-even after TP1 when enabled, and records estimated round-trip fees/slippage in result percentages. Saudi Exchange tick-size bands are applied to entry, stop and target prices.

### Render persistence

The bundled TASI universe survives a fresh deploy because it is part of the repository. Runtime JSON state under `STATE_DIR=data` is not a durable database on Render Free; use an external persistent store if open trades/history must survive every deploy/replacement.


## Tasilab 502 resilience
- Bulk quote chunks default to 20 symbols.
- A bulk 500/502/503/504 is retried once by the HTTP layer.
- If bulk still fails, only the bulk endpoint is cooled down for 5 minutes.
- The scan degrades to bounded single-quote requests (default 60 symbols per scan).
- Repeated scans rotate through the bundled TASI universe while bulk is cooling down.
- A provider-wide circuit opens only after 3 consecutive single-quote 5xx failures, for 5 minutes.
- A Tasilab 429 stops fallback calls and respects the provider cooldown.

## Tasilab diagnostic test

To isolate Tasilab failures without running a market scan, run:

```bash
python -m app.main --test-tasilab
```

The diagnostic performs exactly four small checks (unless authentication fails first):

1. `GET /v1/auth/me`
2. `GET /v1/market/status`
3. `GET /v1/market/quote/1120`
4. `GET /v1/market/quotes?symbols=1120,2222`

It prints HTTP status, latency, Cloudflare detection, `Retry-After`, `CF-Ray`, and a short error preview. The API key is never printed.

Classification examples:

- `HEALTHY` — all checks passed.
- `AUTH_ERROR` — API key/authentication problem.
- `RATE_LIMIT` — provider returned HTTP 429.
- `BULK_ENDPOINT_5XX_ONLY` — single quote works but bulk endpoint is failing.
- `QUOTE_ENDPOINT_5XX` — market status works but single quote endpoint is failing.
- `PROVIDER_OR_UPSTREAM_5XX` — multiple market endpoints are returning 5xx; likely provider/upstream outage.
- `NETWORK_OR_TIMEOUT` — connection or timeout problem from the deployment environment.
- `ENDPOINT_OR_PARAMETER_ERROR` — 400/404/422 on one of the market endpoints.

## Saudi Anti-Fake Momentum v2

The current strategy is optimized for conservative Saudi-market signal discovery rather than high signal frequency. It uses EMA 9/20/50/200, VWAP, ADX/+DI/-DI, RSI, MACD histogram acceleration, ATR, RVOL, volume trend, OBV, Accumulation/Distribution, support/resistance, breakout acceptance, candle body/wick quality and ATR-normalized extension from VWAP/EMA20.

Hard rejects override the score. A failed breakout, weak participation, bearish market context, excessive extension, weak DI control or distribution-style high-volume candle can block a signal even when momentum indicators are positive. Only A/A+ setups are published. It is normal for `/signal` to return no trade.

## Reviewed deployment defaults

- `MIN_SCORE=82`
- `MIN_RR=1.8`
- `SIGNAL_WINDOW_START=10:20`
- `SIGNAL_WINDOW_END=14:30`
- `INTRADAY_MAX_PRICE_GAP_PCT=4`
- `HISTORICAL_INTRADAY_MAX_AGE_MINUTES=45`
- `TASILAB_BULK_CHUNK_SIZE=20`
- `FEE_BPS=15.5` (configurable baseline)

`render.yaml` and `.env.example` are aligned with these defaults.


## Private Telegram Control Menu

The private admin chat uses a persistent Arabic reply keyboard for the main controls (signal scan, market, open trades, performance, report, health, settings, risk, Tasilab diagnostic, pause/resume). Legacy slash commands remain supported as a fallback. Group/channel chats remain publish-only.

## Telegram media/report update

- Approved bundled visuals live under `app/assets/telegram/`.
- New stock signals are published as a photo with the signal text as the caption.
- The original Telegram message ID is stored per public destination so profit/TP updates can reply to the originating signal message.
- If a destination (for example a channel) cannot accept the reply relation, the update falls back to a normal post instead of being lost.
- Daily report: scheduled for 15:05 KSA by default and published as image first, then a separate text summary.
- Weekly report: Thursday after close, image first, then a separate text summary.
- Private admin test menu includes trade, profit reply, daily report, and weekly report previews. These display tests do not call market-data APIs.
- The bundled report images are approved static visual assets; live report statistics are carried by the following text message.

## Manual signal confirmation flow

The private admin control panel does not publish a discovered setup immediately.

1. Press `🔎 فحص فرصة`.
2. If a setup passes all Saudi-market hard filters, the bot sends a **private preview only**.
3. The preview presents `✅ إرسال الصفقة` and `❌ إلغاء الصفقة`.
4. `✅ إرسال الصفقة` reuses the already scanned setup, creates the Paper Trade, publishes it to the configured group/channel, and stores the root Telegram message IDs for later profit replies. **No market API request is repeated during confirmation.**
5. `❌ إلغاء الصفقة` deletes the preview state and does not increment daily signal counts or create an open trade.
6. Pending confirmations expire after `SIGNAL_CONFIRMATION_EXPIRY_MINUTES` (default: 5 minutes).
7. Returning to the main menu also cancels any still-pending preview.

If publication fails for every public Telegram destination, the project rolls back the Paper Trade and daily signal counter instead of leaving an unpublished trade active.
