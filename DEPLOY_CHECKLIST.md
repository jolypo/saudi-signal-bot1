# Render Deployment Checklist

- [ ] ارفع المشروع بدون `.env` الحقيقي.
- [ ] أضف جميع Telegram tokens في Render Environment.
- [ ] أضف `TELEGRAM_CHAT_ID`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_ADMIN_USER_ID`.
- [ ] أضف `SAHMK_API_KEY` و `TASILAB_API_KEY`.
- [ ] `SAHMK_LOCAL_DAILY_LIMIT=100`.
- [ ] `SAHMK_DAILY_SWITCH_LIMIT=90`.
- [ ] `SAHMK_MIN_REQUEST_INTERVAL=6.5`.
- [ ] `PROVIDER_SWITCH_ON_DAILY_LIMIT=true`.
- [ ] `PROVIDER_SWITCH_ON_429=false`.
- [ ] `TELEGRAM_MODE=auto`.
- [ ] `PAPER_MODE=true`.
- [ ] Deploy.
- [ ] تحقق من `/health` أن `active_provider=sahmk` في بداية اليوم.
- [ ] جرّب `/signal` مرة واحدة أثناء السوق.
- [ ] إذا ظهر 429 مؤقت، تأكد أن اللوق يقول `Tasilab NOT activated` أو `temporary SAHMK throttle`.
- [ ] عند وصول SAHMK إلى حد التحويل، تحقق أن `active_provider=tasilab`.

## Provider / Render resilience
- [ ] `app/data/tasi_universe.json` موجود داخل المستودع.
- [ ] `PROVIDER_SWITCH_ON_429=false`.
- [ ] `SAHMK_DAILY_SWITCH_LIMIT=90`.
- [ ] عند ظهور `Temporary security limit` أو burst 429 لا يتم التحويل إلى Tasilab.
- [ ] عند ظهور `daily` أو `IP daily rate limit exceeded` يتم التحويل إلى Tasilab.
- [ ] `/health` يعرض `active_provider` و`universe_cache_size`.


## Tasilab 502 resilience
- Bulk quote chunks default to 20 symbols.
- A bulk 500/502/503/504 is retried once by the HTTP layer.
- If bulk still fails, only the bulk endpoint is cooled down for 5 minutes.
- The scan degrades to bounded single-quote requests (default 60 symbols per scan).
- Repeated scans rotate through the bundled TASI universe while bulk is cooling down.
- A provider-wide circuit opens only after 3 consecutive single-quote 5xx failures, for 5 minutes.
- A Tasilab 429 stops fallback calls and respects the provider cooldown.

## Tasilab troubleshooting

If Tasilab returns 5xx/502 in Render logs, run the dedicated diagnostic when shell access is available:

```bash
python -m app.main --test-tasilab
```

Do not paste API keys into logs or screenshots. The diagnostic does not print the configured key.
