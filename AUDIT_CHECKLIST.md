# API Leak Audit Checklist

| Check | Status |
|---|---|
| Telegram uses webhook, not polling/getUpdates | PASS |
| Webhook URL does not contain secret | PASS |
| Default startup state is SHUTDOWN | PASS |
| SAHMK circuit breaker defaults disabled | PASS |
| SAHMK requires both firewall enabled + explicit allow_scope | PASS |
| Disabled firewall blocks request before HTTP | PASS (self-test) |
| SAHMK logs every outbound attempt before HTTP | PASS |
| SAHMK response/status/error logged | PASS |
| API key never printed; SHA-256 fingerprint only | PASS |
| Yahoo request logged per symbol | PASS |
| HTTP GET/HEAD health requests logged | PASS |
| Telegram command/update logged | PASS |
| Trade-monitor tick/skip reason logged | PASS |
| Scan start/finish logged | PASS |
| `/debug` is zero-API | PASS |
| `/health` is zero-API | PASS |
| Python compileall | PASS |
| GitHub upload file count < 100 | PASS |

## How to identify a leak
Search Render logs for `AUDIT`.

If SAHMK dashboard increments, this process must show an event named `sahmk_http_outbound` before the request leaves the process. It includes `request_no`, endpoint `path`, `reason`, and safe `callers` (file:line:function).

If the dashboard increments but there is **no** matching `sahmk_http_outbound` event and `/health` still shows the same `allowed` count, the request did not originate from this running process. Compare `key_fp` and `instance` across services.

## Arabic Menu checks
- Official BotCommand menu registered at startup with Arabic descriptions.
- Persistent ReplyKeyboard is shown on `/start` and `/help`.
- Arabic button clicks are logged as `telegram_arabic_menu`.
- Buttons reuse existing guarded handlers; they do not bypass SAHMK firewall or market-hours gates.
