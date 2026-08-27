# تشغيل البوت على Render Free

هذه النسخة تحتوي على HTTP health server في `/health` و`/status` وتقرأ `PORT` الذي يوفره Render.

## النشر
1. ارفع المشروع إلى GitHub (بدون ملف `.env`).
2. في Render اختر **New → Web Service** واربط المستودع.
3. اختر Docker، والخطة Free.
4. أضف `TELEGRAM_BOT_TOKEN` و`SAHMK_API_KEY` كـ Environment Variables.
5. يمكن استخدام `render.yaml` كمرجع للإعدادات.
6. Health Check Path: `/health`.

## مهم جدًا عن Sleep
Render Free ينام عند عدم وجود inbound traffic. الكود لا يستطيع إلغاء سياسة المنصة. `/health` فقط يوفّر endpoint يمكن لخدمة خارجية استدعاؤه لإيقاظ الخدمة.

إذا استخدمت Cron خارجي، اجعل الطلب إلى:
`https://YOUR-SERVICE.onrender.com/health`

لا تعتبر الـping ضمانًا لتشغيل Worker دائم؛ هدفه تقليل مدة النوم. كما أن Telegram polling نفسه ليس Webhook.

## الحالة
- SIGNALS ONLY
- PAPER TRADING
- TASI-25
- Scanner كل 30 دقيقة افتراضيًا
- Trade monitor كل 15 دقيقة افتراضيًا
- Health endpoint
- حفظ حالات إشعارات الربح لمنع التكرار
- استئناف الصفقات المفتوحة بعد إعادة التشغيل من SQLite


## API quota behavior (manual scan mode)

- The market scanner does **not** run on a timer.
- A scan starts only when a Telegram user sends `/signals`.
- `/health`, `/open`, `/history`, `/stats`, `/settings`, `/pause`, and `/resume` do not start a market scan.
- `/market` and `/sectors` each call SAHMK only when explicitly requested.
- Open paper trades are still monitored automatically; SAHMK quote calls are made only when at least one paper trade is open.
- Historical TASI-25 candles used by the scanner come from Yahoo Finance, so Yahoo rate limits can still affect a manual `/signals` scan.

## Emergency API controls
- `/pause`: stops new signal scans and requests cancellation of a scan already in progress; open-paper-trade monitoring remains enabled.
- `/shutdown` (alias `/stop`): hard zero-market-data mode. Stops new scans, requests cancellation of an active scan, blocks `/market` and `/sectors`, and disables automatic open-trade quote monitoring. Telegram webhook and `/health` remain available.
- `/resume`: leaves pause/shutdown mode. It does not start a scan; `/signals` is still required.
- Cancellation is cooperative: an HTTP request already in flight cannot be unsent, but no next symbol/request is started after the stop flag is observed.

## API Firewall (quota protection)

This build uses a DEFAULT-DENY firewall inside `data/sahmk.py`.
After every deploy/restart the bot starts in `SHUTDOWN`; no SAHMK request can leave the process until `/resume` is sent. Even after `/resume`, SAHMK calls are only permitted inside explicit scopes for `/signals`, `/market`, `/sectors`, or active-trade monitoring. `/shutdown` returns the bot to zero-market-data mode. `/health` reports SAHMK allowed/blocked counters since boot and never calls SAHMK/Yahoo.

## Diagnostic / API leak audit mode

This build emits structured one-line `AUDIT {...}` logs for:
- process/application startup and shutdown;
- every inbound HTTP/health request (without headers/query/body);
- every Telegram webhook update and command;
- every scheduled trade-monitor tick and why it was skipped;
- every scanner start/finish;
- every Yahoo historical request per symbol;
- every SAHMK allow-scope entry/exit;
- every blocked SAHMK attempt;
- every real outbound SAHMK HTTP request **before it leaves the process**, including safe caller file/line/function information;
- every SAHMK response/status/error.

The SAHMK API key is never printed. Logs use only a 12-character SHA-256 fingerprint (`key_fp`) so you can identify which instance/key generated a request without exposing the key.

The bot starts in `SHUTDOWN` and the SAHMK circuit breaker starts disabled. `/debug` is zero-API and shows recent audit events and counters. `/health` is also zero-API and exposes only safe counters/fingerprint.
