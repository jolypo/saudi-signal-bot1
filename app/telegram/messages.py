from datetime import datetime
from zoneinfo import ZoneInfo


def _fmt(v,d=2):
    try:return f"{float(v):.{d}f}"
    except:return "—"

def _time_ar(value):
    if not value:return "—"
    try:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(ZoneInfo("Asia/Riyadh"))
        return dt.strftime("%d-%m-%Y %H:%M")
    except:return str(value)

def signal_message(t):
    if t.get("probability_status")=="VALIDATED":
        probability=f"📊 الاحتمالية التاريخية: {_fmt(t.get('probability'),1)}% | العينات: {t.get('probability_samples',0)}"
    else:
        probability=f"📊 الاحتمالية: غير موثقة بعد | العينات: {t.get('probability_samples',0)}"
    reasons="\n".join(f"• {x}" for x in t.get("reasons",[])[:8]) or "• توافق الشروط الفنية المطلوبة"
    targets="\n".join(f"• {x}" for x in t.get("target_reasons",[])[:4])
    invalid="\n".join(f"• {x}" for x in t.get("invalidation_reasons",[])[:4])
    return (
        "🚨 فرصة تداول ورقية جديدة\n\n"
        f"السهم: {t['name']}\nالرمز: {t['symbol']}\n\n"
        f"🧭 نوع الصفقة: {t.get('trade_type','—')}\n"
        f"⏳ المدة التقديرية: {t.get('expected_tp3','—')}\n\n"
        f"💰 منطقة الدخول: {_fmt(t['entry_low'])} – {_fmt(t['entry_high'])}\n"
        f"🛑 وقف الخسارة: {_fmt(t['sl'])}\n\n"
        f"🎯 الهدف الأول: {_fmt(t['tp1'])}\n🎯 الهدف الثاني: {_fmt(t['tp2'])}\n🎯 الهدف الثالث: {_fmt(t['tp3'])}\n\n"
        f"⭐ قوة الإشارة: {_fmt(t['score'],1)}/100 | التصنيف: {t.get('grade','A')}\n"
        f"⚖️ العائد مقابل المخاطرة: 1 : {_fmt(t['rr_tp1'])}\n"
        f"🛡️ مستوى المخاطرة: {t.get('risk_level','—')}\n{probability}\n"
        f"📈 حالة السوق: {t.get('market_regime','—')}\n🏦 القطاع: {t.get('sector','غير متاح')}\n\n"
        f"📌 سبب توقع الصعود:\n{reasons}\n\n"
        f"🎯 سبب اختيار الأهداف:\n{targets}\n\n"
        f"🛑 سبب وقف الخسارة:\n• أسفل مستوى فني محسوب من ATR والدعم مع هامش يمنع الخروج من التذبذب الطبيعي.\n\n"
        f"⚠️ متى يبطل التوقع؟\n{invalid}\n\n"
        "📡 البيانات: سهمك للسعر والمتابعة + Yahoo للبيانات التاريخية البحثية\n"
        "⚠️ تداول ورقي فقط — لا يوجد تنفيذ حقيقي\n\n"
        f"🕒 آخر تحديث للبيانات: سهمك {_time_ar(t.get('quote_updated_at'))} | التاريخية {_time_ar(t.get('historical_updated_at'))}"
    )

def profit_message(t,price,delta):
    pct=(price-t["entry"])/t["entry"]*100
    return f"🟢 تحديث الأرباح\n\n{t['name']} — {t['symbol']}\nنوع الصفقة: {t.get('trade_type','—')}\nالدخول: {_fmt(t['entry'])}\nالسعر الحالي: {_fmt(price)}\nالحركة: {delta:+.2f} ريال\nالربح: {pct:+.2f}%\nالحالة: الصفقة مستمرة."

def loss_message(t,price):
    pct=(price-t["entry"])/t["entry"]*100
    return f"🔴 وقف الخسارة تحقق\n\n{t['name']} — {t['symbol']}\nنوع الصفقة: {t.get('trade_type','—')}\nالدخول: {_fmt(t['entry'])}\nالخروج: {_fmt(price)}\nالنتيجة: {pct:+.2f}%\nالحالة: الصفقة مغلقة."

def near_sl_message(t,price):
    stop=t.get("trailing_stop") or t.get("sl")
    return f"⚠️ اقتراب من وقف الخسارة\n\nالسهم: {t['name']} ({t['symbol']})\nالسعر الحالي: {_fmt(price)}\nوقف الخسارة الفعّال: {_fmt(stop)}\nالحالة: الصفقة ما زالت مفتوحة."

def tp_message(t,tp_name,price):
    pct=(price-t["entry"])/t["entry"]*100
    names={"TP1":"الهدف الأول","TP2":"الهدف الثاني","TP3":"الهدف الثالث"}
    return f"🎯 {names.get(tp_name,tp_name)} تحقق\n\nالسهم: {t['name']} ({t['symbol']})\nنوع الصفقة: {t.get('trade_type','—')}\nالدخول: {_fmt(t['entry'])}\nالسعر: {_fmt(price)}\nالربح: {pct:+.2f}%\nالحالة: {names.get(tp_name,tp_name)} تحقق."


def signal_caption(t):
    """Compact photo caption kept safely below Telegram's photo-caption limit."""
    return (
        "🚨 فرصة تداول ورقية جديدة\n\n"
        f"السهم: {t.get('name', '—')} ({t.get('symbol', '—')})\n"
        f"🧭 {t.get('trade_type', '—')}\n"
        f"💰 الدخول: {_fmt(t.get('entry_low'))} – {_fmt(t.get('entry_high'))}\n"
        f"🛑 SL: {_fmt(t.get('sl'))}\n"
        f"🎯 TP1: {_fmt(t.get('tp1'))} | TP2: {_fmt(t.get('tp2'))} | TP3: {_fmt(t.get('tp3'))}\n"
        f"⭐ Score: {_fmt(t.get('score'),1)}/100 | {t.get('grade','A')}\n"
        f"⚖️ R/R: 1 : {_fmt(t.get('rr_tp1'))}\n"
        "⚠️ Paper Trading فقط"
    )
