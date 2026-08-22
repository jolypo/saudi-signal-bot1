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
