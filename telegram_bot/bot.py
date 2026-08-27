from __future__ import annotations
import asyncio
import threading
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config.settings import settings
from config.universe import TASI_25, BY_SYMBOL
from database.models import db, User, Trade
from data.sahmk import SahmkClient
from charts.signal_card import make_signal_chart
from scanner.universe_scanner import UniverseScanner
from trade_tracker.tracker import TradeTracker
from health_server import start_health_server, update_state

Session = db(settings.database_url)
client = SahmkClient(settings.sahmk_base_url, settings.sahmk_api_key)
tracker = TradeTracker(Session)
scanner = None  # Lazy: constructed only after /signals passes the local market-hours gate.
paused = False
hard_stopped = True  # Safe default after every restart/deploy: API remains locked until /resume.
scan_stop_event = threading.Event()
scan_lock = asyncio.Lock()


def saudi_market_session_status(now=None):
    """Return (is_open, message) without making any API/network request.

    Saudi Exchange regular equity trading is Sunday-Thursday, 10:00-15:00 KSA.
    This guard intentionally errs on the side of preserving API quota outside the session.
    Exchange holidays are not inferred here; an optional live check can be added later if quota allows.
    """
    tz = ZoneInfo(settings.timezone or "Asia/Riyadh")
    now = now.astimezone(tz) if now is not None else datetime.now(tz)

    # Python weekday: Monday=0 ... Friday=4, Saturday=5, Sunday=6
    if now.weekday() in (4, 5):
        return False, (
            "🔴 السوق السعودي مغلق اليوم.\n"
            "أيام التداول: الأحد إلى الخميس.\n"
            "⏰ أعد المحاولة عند الساعة 10:00 صباحًا بتوقيت السعودية."
        )

    open_at = time(10, 0)
    close_at = time(15, 0)
    local_time = now.time().replace(tzinfo=None)

    if local_time < open_at:
        return False, (
            "🔴 السوق السعودي مغلق حاليًا.\n"
            f"الوقت الآن: {now:%H:%M} بتوقيت السعودية.\n"
            "⏰ يبدأ التداول الساعة 10:00 صباحًا. حاول عند الافتتاح."
        )

    if local_time >= close_at:
        return False, (
            "🔴 السوق السعودي مغلق حاليًا.\n"
            f"الوقت الآن: {now:%H:%M} بتوقيت السعودية.\n"
            "⏰ انتهت جلسة اليوم الساعة 15:00. حاول الساعة 10:00 صباح يوم التداول القادم."
        )

    return True, (
        f"🟢 السوق مفتوح — {now:%H:%M} بتوقيت السعودية."
    )


def market_regime(summary):
    x = summary.get('summary', summary)
    mood = str(x.get('market_mood', 'NEUTRAL')).upper()
    ch = float(x.get('index_change_percent') or 0)
    if 'BULL' in mood or ch >= .35:
        return 'BULLISH'
    if 'BEAR' in mood or ch <= -.35:
        return 'BEARISH'
    return 'NEUTRAL'


async def subscribe(update):
    s = Session()
    u = s.query(User).filter(User.telegram_id == update.effective_user.id).first()
    if not u:
        u = User(telegram_id=update.effective_user.id, username=update.effective_user.username, active=True)
        s.add(u)
    else:
        u.active = True
        u.username = update.effective_user.username
    s.commit(); s.close()


async def subscribers():
    s = Session(); ids = [u.telegram_id for u in s.query(User).filter(User.active == True).all()]; s.close(); return ids


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await subscribe(update)
    await update.message.reply_text('🤖 بوت إشارات السوق السعودي\n\nتم تسجيلك لاستقبال الإشارات.\nSIGNALS ONLY + PAPER TRADING\n\n/help للأوامر.')


async def help_cmd(update, context):
    await update.message.reply_text('/signals\n/open\n/history\n/stats\n/market\n/sectors\n/settings\n/pause\n/shutdown\n/resume\n/health')


async def market(update, context):
    if hard_stopped:
        return await update.message.reply_text('🛑 النظام متوقف بالكامل — 0 طلبات بيانات سوق. استخدم /resume للتشغيل.')
    try:
        with client.allow_scope('/market'):
            r = client.market_summary()
        regime = market_regime(r)
        await update.message.reply_text(f"📊 حالة السوق السعودي\n\nTASI: {r.get('index_value','-')} ({r.get('index_change_percent','-')}%)\nMarket Regime: {regime}\nالصاعدة: {r.get('advancing','-')}\nالهابطة: {r.get('declining','-')}\nالحجم: {r.get('total_volume','-')}")
    except Exception as e:
        await update.message.reply_text(f'⚠️ تعذر جلب بيانات السوق: {e}')


async def sectors(update, context):
    if hard_stopped:
        return await update.message.reply_text('🛑 النظام متوقف بالكامل — 0 طلبات بيانات سوق. استخدم /resume للتشغيل.')
    try:
        with client.allow_scope('/sectors'):
            data = client.sectors()
        rows = data.get('sectors', data.get('results', []))
        rows = sorted(rows, key=lambda x: float(x.get('change_percent') or 0), reverse=True)
        text = '📊 قوة القطاعات\n\n' + '\n'.join(f"{i+1}. {r.get('sector_name_ar') or r.get('sector_name') or r.get('name')}: {float(r.get('change_percent') or 0):+.2f}%" for i, r in enumerate(rows[:10]))
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f'⚠️ {e}')


async def settings_cmd(update, context):
    await update.message.reply_text(f'⚙️ الإعدادات\nUniverse: 25 سهم\nMinimum Score: {settings.min_score}\nMinimum Probability: {settings.min_probability}%\nMax signals/day: {settings.max_new_signals_per_day}\nScan: يدوي فقط عبر /signals\nHistory: {settings.history_period} / {settings.history_interval}\nPaper: {settings.paper_mode}\nRender PORT: {settings.port}')


async def pause(update, context):
    global paused
    paused = True
    scan_stop_event.set()
    await update.message.reply_text('⏸️ تم إيقاف البحث عن إشارات جديدة. إذا كان فحص جارٍ فسيتوقف بعد الطلب الحالي. متابعة الصفقات الورقية مستمرة.')


async def shutdown(update, context):
    global paused, hard_stopped
    paused = True
    hard_stopped = True
    scan_stop_event.set()
    await update.message.reply_text('🛑 تم إيقاف بيانات السوق بالكامل. لا فحص، لا SAHMK، لا Yahoo، ولا مراقبة صفقات. Telegram و /health فقط يعملان. استخدم /resume للتشغيل.')


async def resume(update, context):
    global paused, hard_stopped
    paused = False
    hard_stopped = False
    scan_stop_event.clear()
    await update.message.reply_text('▶️ تم استئناف النظام. لن يبدأ أي فحص حتى ترسل /signals.')


async def health(update, context):
    st = __import__('health_server').get_state()
    mode = 'SHUTDOWN' if hard_stopped else ('PAUSED' if paused else 'READY')
    api = client.stats()
    await update.message.reply_text(f"🟢 البوت يعمل\nSystem: {mode}\nAPI Firewall: DEFAULT-DENY\nSAHMK allowed since boot: {api['allowed']}\nSAHMK blocked since boot: {api['blocked']}\nMode: SIGNALS ONLY + PAPER TRADING\nUniverse: TASI 25\nآخر فحص: {st.get('last_scan_finished') or '-'}\nآخر فحص ناجح: {st.get('last_scan_ok')}\nخطأ آخر فحص: {st.get('last_scan_error') or 'لا يوجد'}")


async def signals(update, context):
    await subscribe(update)

    if hard_stopped:
        await update.message.reply_text('🛑 النظام متوقف بالكامل. استخدم /resume أولًا.')
        return

    # Zero-API gate: never call SAHMK or Yahoo while the exchange is closed.
    is_open, session_message = saudi_market_session_status()
    if not is_open:
        await update.message.reply_text(session_message)
        return

    if paused:
        await update.message.reply_text('⏸️ الإشارات الجديدة موقوفة حاليًا. استخدم /resume ثم /signals.')
        return

    if scan_lock.locked():
        await update.message.reply_text('⏳ يوجد فحص جارٍ بالفعل. انتظر انتهاءه قبل طلب فحص جديد.')
        return

    await update.message.reply_text(
        f'{session_message}\n🔎 بدأ الفحص اليدوي على قائمة TASI-25. قد يستغرق عدة دقائق.'
    )
    await run_scan(context, manual_chat_id=update.effective_chat.id)


async def open_cmd(update, context):
    rows = tracker.open_trades()
    if not rows:
        return await update.message.reply_text('لا توجد صفقات مفتوحة.')
    await update.message.reply_text('🟢 الصفقات المفتوحة\n\n' + '\n'.join(f'{r.symbol} | Entry {r.entry:.2f} | TP1 {r.tp1:.2f} | TP2 {r.tp2:.2f} | SL {r.sl:.2f}' for r in rows))


async def history(update, context):
    s = Session(); rows = s.query(Trade).order_by(Trade.id.desc()).limit(10).all(); s.close()
    text = '📚 آخر الصفقات\n\n' + ('\n'.join(f'{r.symbol}: {r.status} {r.result if r.result is not None else "-"}%' for r in rows) if rows else 'لا توجد بيانات.')
    await update.message.reply_text(text)


async def stats(update, context):
    s = Session(); rows = s.query(Trade).filter(Trade.status == 'CLOSED').all(); s.close()
    if not rows:
        return await update.message.reply_text('📈 لا توجد صفقات مغلقة بعد.')
    wins = [r for r in rows if (r.result or 0) > 0]; losses = [r for r in rows if (r.result or 0) <= 0]
    avgw = sum(r.result for r in wins) / len(wins) if wins else 0; avgl = sum(r.result for r in losses) / len(losses) if losses else 0
    pf = (sum(r.result for r in wins) / abs(sum(r.result for r in losses))) if losses and sum(r.result for r in losses) != 0 else 0
    await update.message.reply_text(f'📈 إحصائيات\nالصفقات: {len(rows)}\nالرابحة: {len(wins)}\nالخاسرة: {len(losses)}\nWin Rate: {len(wins)/len(rows)*100:.1f}%\nمتوسط الربح: {avgw:.2f}%\nمتوسط الخسارة: {avgl:.2f}%\nProfit Factor: {pf:.2f}')


async def run_scan(context, manual_chat_id=None):
    global paused, hard_stopped, scanner
    if hard_stopped:
        return
    if paused:
        return
    if scan_lock.locked():
        return
    async with scan_lock:
        started = datetime.now(timezone.utc).isoformat()
        update_state(last_scan_started=started, last_scan_error=None)
        try:
            scan_stop_event.clear()
            # Construct the historical-data scanner only on an explicit, in-session /signals request.
            # This guarantees importing/using yfinance cannot happen during process startup or /health.
            if scanner is None:
                scanner = UniverseScanner(settings.min_score, settings.min_probability, settings.min_rr)
            if hard_stopped or paused or scan_stop_event.is_set():
                return
            def _fetch_market_summary():
                with client.allow_scope('/signals:market_summary'):
                    return client.market_summary()
            summary = await asyncio.to_thread(_fetch_market_summary)
            if hard_stopped or paused or scan_stop_event.is_set():
                return
            regime = market_regime(summary)
            bullish = regime != 'BEARISH'
            results = await asyncio.to_thread(scanner.scan, bullish, scan_stop_event)
            if hard_stopped or paused or scan_stop_event.is_set():
                if manual_chat_id:
                    await context.bot.send_message(manual_chat_id, "🛑 تم إلغاء الفحص.")
                return
            s = Session(); today = datetime.now().date()
            sent_today = s.query(Trade).filter(Trade.entry_time >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)).count(); s.close()
            quota = max(0, settings.max_new_signals_per_day - sent_today)
            signal_sent = None
            for item in results:
                if quota <= 0: break
                sig = item.signal
                if not sig: continue
                s = Session(); dup = s.query(Trade).filter(Trade.symbol == sig.symbol, Trade.entry_time >= datetime.now(timezone.utc) - timedelta(minutes=settings.duplicate_cooldown_min)).first(); s.close()
                if dup: continue
                chart = make_signal_chart(sig, None)
                trade = tracker.open(sig, regime, BY_SYMBOL.get(sig.symbol, {}).get('sector', ''))
                text = (f"🚨 فرصة تداول جديدة\n\nالسهم: {sig.name}\n\n📈 الاتجاه: شراء\n\n💰 منطقة الدخول: {sig.entry_low:.2f} – {sig.entry_high:.2f}\n\n🎯 TP1: {sig.tp1:.2f}\n🎯 TP2: {sig.tp2:.2f}\n🎯 TP3: {sig.tp3:.2f}\n\n🛑 وقف الخسارة: {sig.sl:.2f}\n\n📊 Probability: {sig.probability:.0f}%\n⚖️ Risk/Reward: 1 : {sig.rr:.2f}\n\nسبب الإشارة:\n" + ' + '.join(sig.reasons) + f"\n\nPaper Trade ID: {trade.id}")
                ids = [manual_chat_id] if manual_chat_id else await subscribers()
                for chat_id in ids:
                    try:
                        with open(chart, 'rb') as photo:
                            await context.bot.send_photo(chat_id, photo=photo, caption=text[:1024])
                        await context.bot.send_message(chat_id, text)
                    except Exception:
                        pass
                signal_sent = sig.symbol
                quota -= 1
            update_state(last_scan_finished=datetime.now(timezone.utc).isoformat(), last_scan_ok=True, last_signal=signal_sent)
        except Exception as e:
            update_state(last_scan_finished=datetime.now(timezone.utc).isoformat(), last_scan_ok=False, last_scan_error=str(e)[:500])
            if manual_chat_id:
                await context.bot.send_message(manual_chat_id, f'⚠️ فشل الفحص: {e}')


async def monitor_trades(context):
    if hard_stopped:
        return
    update_state(last_trade_monitor=datetime.now(timezone.utc).isoformat())
    # Zero-API gate for the automatic monitor too. No quote calls outside TASI session.
    is_open, _ = saudi_market_session_status()
    if not is_open:
        return
    rows = tracker.open_trades()
    if not rows:
        return
    ids = await subscribers()
    for t in rows:
        try:
            def _fetch_quote(symbol=t.symbol):
                with client.allow_scope(f'trade_monitor:{symbol}'):
                    return client.quote(symbol)
            q = await asyncio.to_thread(_fetch_quote); price = float(q.get('price'))
            events = tracker.update(t, price, settings.profit_levels, settings.trailing_stop_enabled)
            for kind, pnl in events:
                if kind.startswith('PROFIT_'):
                    msg = f'📈 تحديث ربح\nالسهم: {t.name}\nالسعر: {price:.2f}\nالربح: {pnl:+.2f}%'
                elif kind == 'TP1': msg = f'🎯 TP1 تحقق\nالسهم: {t.name}\nالسعر: {price:.2f}\nالربح: {pnl:+.2f}%'
                elif kind == 'TP2': msg = f'🎯 TP2 تحقق\nالسهم: {t.name}\nالسعر: {price:.2f}\nالربح: {pnl:+.2f}%'
                elif kind == 'TP3': msg = f'🎯 TP3 تحقق — الصفقة مغلقة\nالسهم: {t.name}\nالخروج: {price:.2f}\nالنتيجة: {pnl:+.2f}%'
                else: msg = f'🔴 وقف الخسارة\nالسهم: {t.name}\nالخروج: {price:.2f}\nالنتيجة: {pnl:+.2f}%'
                for chat_id in ids:
                    try: await context.bot.send_message(chat_id, msg)
                    except Exception: pass
        except Exception:
            pass



def build_application():
    app = Application.builder().token(settings.telegram_bot_token).build()
    handlers = [('start', start), ('help', help_cmd), ('market', market), ('sectors', sectors), ('settings', settings_cmd), ('pause', pause), ('shutdown', shutdown), ('stop', shutdown), ('resume', resume), ('health', health), ('signals', signals), ('open', open_cmd), ('history', history), ('stats', stats)]
    for cmd, fn in handlers:
        app.add_handler(CommandHandler(cmd, fn))
    # No automatic scanner. Only /signals can call run_scan().
    app.job_queue.run_repeating(monitor_trades, interval=settings.trade_monitor_seconds, first=60)
    return app
