# Saudi Signal Bot — TASI 25

بوت Telegram عربي لإشارات الأسهم السعودية، يعمل **SIGNALS ONLY + PAPER TRADING** ولا ينفذ أي شراء أو بيع حقيقي.

## النسخة الحالية

- Universe ثابت من 25 سهمًا سعوديًا عالي الأهمية والسيولة نسبيًا.
- تحليل TASI وحالة السوق قبل إصدار الإشارة.
- Screener: EMA 9/20/50/200, VWAP, RSI, MACD, ATR, Volume/Relative Volume, Momentum, Support/Resistance.
- Entry Zone + ATR/Structure SL + TP1/TP2/TP3 + Risk/Reward.
- Probability **تجريبية إحصائية وليست رقمًا من AI**: تحسب من حالات تاريخية مشابهة وتُرفض الإشارة إذا لم يتوفر حد أدنى من العينات.
- Paper Trade Record + متابعة TP1/TP2/TP3/SL.
- Telegram subscribers: أي مستخدم يضغط /start يصبح مشتركًا في الإشارات.
- منع التكرار وMax signals/day.
- SQLite database.
- رسم إشارة مناسب للجوال.
- SAHMK للبيانات الحالية/السوق، وYahoo Finance عبر yfinance كمصدر تاريخي مجاني منفصل للأبحاث/backtest.

## مهم جدًا عن البيانات المجانية

SAHMK يوفر quote فردي وmarket summary مجانًا، بينما Historical OHLCV موثق ضمن Starter+. لذلك لا يدّعي هذا المشروع أن SAHMK Free وحده يوفر التاريخ المطلوب للمؤشرات. المصدر التاريخي الاختياري هنا هو yfinance، وهو مصدر غير رسمي ويجب التعامل معه كطبقة بحث قابلة للاستبدال.

## تشغيل Windows

```powershell
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
python main.py
```

## تشغيل Docker

```bash
docker compose up -d --build
docker compose logs -f
```

## الأوامر

/start /signals /open /history /stats /market /sectors /settings /help /pause /resume /health

## إعدادات مهمة في .env

- `POLL_SECONDS=1800` — افتراضيًا كل 30 دقيقة لتقليل استهلاك SAHMK المجاني.
- `MIN_SCORE=72`
- `MIN_PROBABILITY=65`
- `MAX_NEW_SIGNALS_PER_DAY=5`
- `DUPLICATE_COOLDOWN_MIN=180`
- `MIN_RR=1.8`
- `HISTORY_PERIOD=60d`
- `HISTORY_INTERVAL=15m`
- `PAPER_MODE=true`

## 24/7

شغل Docker على جهاز/VPS يبقى متصلًا. البوت نفسه لا يضع أي تنفيذ حقيقي.

## أمان

`.env` يحتوي الأسرار محليًا ولا يجب رفعه إلى GitHub. غيّر Telegram Bot Token وSAHMK API Key إذا تم نشرهما في مكان عام.
