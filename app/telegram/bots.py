import hashlib
import hmac
import os

from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.telegram.messages import (
    loss_message,
    near_sl_message,
    signal_message,
    signal_caption,
)


class TelegramBots:
    def __init__(self, settings):
        self.s = settings

        self.signal = Bot(
            settings.signal_bot_token
        )

        self.profit = Bot(
            settings.profit_bot_token
        )

        self.loss = Bot(
            settings.loss_bot_token
        )

        self.report = Bot(
            settings.report_bot_token
        )

        self.application = None
        self.service = None
        self._last_test_trade_message_id = None

        self.mode = self._resolve_mode()

        self.webhook_secret = hashlib.sha256(
            settings.signal_bot_token.encode(
                "utf-8"
            )
        ).hexdigest()[:48]

    # =========================================================
    # MODE
    # =========================================================

    def _resolve_mode(self):
        configured = str(
            getattr(
                self.s,
                "telegram_mode",
                "auto",
            )
        ).strip().lower()

        if configured in {
            "polling",
            "webhook",
        }:
            return configured

        return (
            "webhook"
            if os.getenv("RENDER_EXTERNAL_URL")
            else "polling"
        )

    # =========================================================
    # SERVICE
    # =========================================================

    def attach_service(self, service):
        self.service = service

    # =========================================================
    # PUBLIC DESTINATIONS
    # =========================================================

    def _public_chat_ids(self):
        """
        الأماكن العامة التي تستقبل:
        - الإشارات
        - تحديثات الأسعار
        - TP
        - SL
        - التقارير المجدولة

        1) القروب
        2) القناة
        """

        destinations = []

        group_id = getattr(
            self.s,
            "telegram_chat_id",
            None,
        )

        channel_id = getattr(
            self.s,
            "telegram_channel_id",
            None,
        )

        if group_id:
            destinations.append(
                int(group_id)
            )

        if channel_id:
            channel_id = int(
                channel_id
            )

            if channel_id not in destinations:
                destinations.append(
                    channel_id
                )

        return destinations

    # =========================================================
    # PRIVATE ADMIN MENU
    # =========================================================

    def _admin_menu(self):
        """Persistent Arabic control panel for the private admin chat."""
        return ReplyKeyboardMarkup(
            [
                ["🔎 فحص فرصة", "📈 حالة السوق"],
                ["📂 الصفقات المفتوحة", "📊 الأداء"],
                ["🧾 تقرير يومي", "📅 تقرير أسبوعي"],
                ["🩺 صحة النظام", "📡 حالة النظام"],
                ["⚙️ الإعدادات", "🛡️ المخاطر"],
                ["🧪 قائمة الاختبارات", "🧪 اختبار Tasilab"],
                ["⏸️ إيقاف الإشارات", "▶️ استئناف الإشارات"],
                ["ℹ️ المساعدة", "👤 معرفي"],
            ],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="اختر من لوحة التحكم",
        )

    def _tests_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["🧪 اختبار صفقة", "🧪 اختبار تحديث أرباح"],
                ["🧪 اختبار تقرير يومي", "🧪 اختبار تقرير أسبوعي"],
                ["⬅️ رجوع للقائمة الرئيسية"],
            ],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="اختبارات العرض الخاصة",
        )

    def _confirm_signal_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["✅ إرسال الصفقة", "❌ إلغاء الصفقة"],
                ["⬅️ رجوع للقائمة الرئيسية"],
            ],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="أكد نشر الصفقة أو ألغها",
        )

    async def _menu_reply(self, update, text):
        if update.effective_message:
            await update.effective_message.reply_text(
                text,
                reply_markup=self._admin_menu(),
            )

    # =========================================================
    # SAFE PUBLIC BROADCAST
    # =========================================================

    async def _broadcast_text(
        self,
        bot,
        text,
    ):
        """
        إرسال نص للقروب والقناة.

        فشل جهة لا يمنع الجهة الثانية.
        """

        success = 0

        for chat_id in self._public_chat_ids():

            try:

                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                )

                success += 1

            except Exception as exc:

                print(
                    "[telegram] broadcast text "
                    f"failed chat={chat_id}: "
                    f"{exc!r}"
                )

        return success

    async def _broadcast_photo(
        self,
        bot,
        image_path,
        caption=None,
    ):
        """
        إرسال صورة للقروب والقناة.
        """

        success = 0

        for chat_id in self._public_chat_ids():

            try:

                with open(
                    image_path,
                    "rb",
                ) as fh:

                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=fh,
                        caption=caption,
                    )

                success += 1

            except Exception as exc:

                print(
                    "[telegram] broadcast photo "
                    f"failed chat={chat_id}: "
                    f"{exc!r}"
                )

        return success

    async def _broadcast_photo_with_ids(self, bot, image_path, caption=None):
        """Broadcast a photo and return {chat_id: message_id} for reply threading."""
        sent = {}
        for chat_id in self._public_chat_ids():
            try:
                with open(image_path, "rb") as fh:
                    msg = await bot.send_photo(chat_id=chat_id, photo=fh, caption=caption)
                sent[str(chat_id)] = int(msg.message_id)
            except Exception as exc:
                print(f"[telegram] broadcast photo/id failed chat={chat_id}: {exc!r}")
        return sent

    async def _broadcast_reply(self, bot, text, trade, image_path=None):
        """Reply to the original signal message in each public destination."""
        success = 0
        ids = (trade or {}).get("signal_message_ids", {}) or {}
        for chat_id in self._public_chat_ids():
            reply_id = ids.get(str(chat_id)) or ids.get(chat_id)
            try:
                kwargs = dict(
                    chat_id=chat_id,
                    reply_to_message_id=int(reply_id) if reply_id else None,
                    allow_sending_without_reply=True,
                )
                if image_path:
                    with open(image_path, "rb") as fh:
                        await bot.send_photo(photo=fh, caption=text, **kwargs)
                else:
                    await bot.send_message(text=text, **kwargs)
                success += 1
            except Exception as exc:
                print(f"[telegram] reply broadcast failed chat={chat_id}: {exc!r}; trying non-reply fallback")
                try:
                    if image_path:
                        with open(image_path, "rb") as fh:
                            await bot.send_photo(chat_id=chat_id, photo=fh, caption=text)
                    else:
                        await bot.send_message(chat_id=chat_id, text=text)
                    success += 1
                except Exception as fallback_exc:
                    print(f"[telegram] reply fallback failed chat={chat_id}: {fallback_exc!r}")
        return success

    # =========================================================
    # PRIVATE ADMIN SEND
    # =========================================================

    async def send_admin_text(
        self,
        text,
    ):
        """
        إرسال نص للمشرف في الخاص.
        """

        await self.signal.send_message(
            chat_id=int(
                self.s.telegram_admin_user_id
            ),
            text=text,
        )

    async def send_admin_report(self, text=None, image_path=None):
        """Send report preview to admin: image first, then separate text message."""
        admin_id = int(self.s.telegram_admin_user_id)
        if image_path:
            with open(image_path, "rb") as fh:
                await self.signal.send_photo(chat_id=admin_id, photo=fh)
        if text:
            await self.signal.send_message(chat_id=admin_id, text=text)

    async def send_admin_signal_preview(self, trade):
        """Private preview only; nothing is published or registered yet."""
        admin_id = int(self.s.telegram_admin_user_id)
        image_path = str(getattr(self.s, "trade_card_image", "app/assets/telegram/trade_card.png"))
        with open(image_path, "rb") as fh:
            msg = await self.signal.send_photo(
                chat_id=admin_id,
                photo=fh,
                caption="🟡 معاينة قبل النشر — لم تُرسل للقروب بعد\n\n" + signal_caption(trade),
                reply_markup=self._confirm_signal_menu(),
            )
        # Full analysis follows as a reply so Telegram's 1024-char photo caption limit is never exceeded.
        await self.signal.send_message(
            chat_id=admin_id,
            text=signal_message(trade),
            reply_to_message_id=msg.message_id,
            allow_sending_without_reply=True,
        )
        return int(msg.message_id)

    # =========================================================
    # CONNECTION TEST
    # =========================================================

    async def test(self):
        """
        اختبار البوتات الأربعة.
        """

        await self._broadcast_text(
            self.signal,
            "🟢 SIGNAL BOT — اتصال ناجح",
        )

        await self._broadcast_text(
            self.profit,
            "🟡 PROFIT BOT — اتصال ناجح",
        )

        await self._broadcast_text(
            self.loss,
            "🔴 LOSS BOT — اتصال ناجح",
        )

        await self._broadcast_text(
            self.report,
            "📊 REPORT BOT — اتصال ناجح",
        )

    # =========================================================
    # SIGNAL BOT PUBLIC OUTPUT
    # =========================================================

    async def send_signal(self, text, image_path=None, trade=None):
        """Publish signal safely and return the photo/text root message ids for later replies."""
        sent = {}
        for chat_id in self._public_chat_ids():
            try:
                if image_path:
                    caption = signal_caption(trade or {}) if trade else "🚨 فرصة تداول ورقية جديدة"
                    with open(image_path, "rb") as fh:
                        root = await self.signal.send_photo(chat_id=chat_id, photo=fh, caption=caption)
                    # Detailed analysis is linked to the photo because Telegram photo captions are capped at 1024 chars.
                    await self.signal.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_to_message_id=root.message_id,
                        allow_sending_without_reply=True,
                    )
                else:
                    root = await self.signal.send_message(chat_id=chat_id, text=text)
                sent[str(chat_id)] = int(root.message_id)
            except Exception as exc:
                print(f"[telegram] signal send failed chat={chat_id}: {exc!r}")
        return sent

    # =========================================================
    # PROFIT BOT PUBLIC OUTPUT
    # =========================================================

    async def send_profit(self, text, trade=None, image_path=None):
        """Profit/TP updates reply to the original signal when trade metadata exists."""
        if trade:
            return await self._broadcast_reply(self.profit, text, trade, image_path=image_path)
        if image_path:
            return await self._broadcast_photo(self.profit, image_path, caption=text)
        return await self._broadcast_text(self.profit, text)

    # =========================================================
    # LOSS BOT PUBLIC OUTPUT
    # =========================================================

    async def send_loss(
        self,
        text,
    ):
        """
        تحديثات وقف الخسارة:
        القروب + القناة
        """

        return await self._broadcast_text(
            self.loss,
            text,
        )

    async def send_loss_for_trade(
        self,
        trade,
        price,
    ):
        await self.send_loss(
            loss_message(
                trade,
                price,
            )
        )

    async def send_near_sl(
        self,
        trade,
        price,
    ):
        await self.send_loss(
            near_sl_message(
                trade,
                price,
            )
        )

    # =========================================================
    # REPORT BOT PUBLIC OUTPUT
    # =========================================================

    async def send_report(self, text=None, image_path=None):
        """Scheduled report: publish image first, then a separate report text."""
        total = 0
        if image_path:
            total += await self._broadcast_photo(self.report, image_path, caption=None)
        if text:
            total += await self._broadcast_text(self.report, text)
        return total

    # =========================================================
    # MARKET CLOSE
    # =========================================================

    async def send_market_close(
        self,
        local_time_text,
    ):
        """
        إشعار إغلاق السوق:
        للمشرف في الخاص فقط.

        لا يتم نشر رسالة إغلاق السوق
        في القروب أو القناة.
        """

        text = (
            "🔔 السوق أغلق اليوم\n\n"
            f"التاريخ والوقت: "
            f"{local_time_text}\n\n"
            "📊 TASI — انتهت جلسة التداول اليوم.\n"
            "📡 البيانات: SAHMK delayed"
        )

        await self.send_admin_text(
            text
        )

    # =========================================================
    # CHAT CHECKS
    # =========================================================

    def _is_private_chat(
        self,
        update: Update,
    ):
        return bool(
            update.effective_chat
            and update.effective_chat.type
            == ChatType.PRIVATE
        )

    def _is_admin_user(
        self,
        update: Update,
    ):
        return bool(
            update.effective_user
            and int(
                update.effective_user.id
            )
            == int(
                self.s.telegram_admin_user_id
            )
        )

    # =========================================================
    # COMMAND ACCESS
    # =========================================================

    def _is_allowed_chat(
        self,
        update: Update,
    ):
        """
        جميع أوامر التحكم تعمل فقط:
        - في الخاص
        - للمستخدم الإداري المحدد

        القروب والقناة للنشر فقط.
        """

        return (
            self._is_private_chat(update)
            and self._is_admin_user(update)
        )

    async def _safe_reply(
        self,
        update,
        text,
    ):
        if update.effective_message:

            await update.effective_message.reply_text(
                text
            )

    async def _guard(
        self,
        update,
    ):
        if self._is_allowed_chat(update):
            return True

        # لا نرسل أي رد داخل القروب
        # حتى يبقى Feed نظيف.
        if not self._is_private_chat(update):
            return False

        await self._safe_reply(
            update,
            "🔒 هذا البوت غير متاح لهذا الحساب.",
        )

        return False

    # =========================================================
    # ADMIN
    # =========================================================

    async def _admin_only(
        self,
        update,
    ):
        return (
            self._is_private_chat(update)
            and self._is_admin_user(update)
        )

    # =========================================================
    # MY ID
    # =========================================================

    async def myid(
        self,
        update,
        context,
    ):
        """
        يعرض Telegram User ID
        للمستخدم في الخاص.
        """

        if (
            not self._is_private_chat(update)
            or not update.effective_user
        ):
            return

        await self._safe_reply(
            update,
            "👤 Telegram User ID الخاص بك:\n"
            f"{update.effective_user.id}",
        )

    # =========================================================
    # START
    # =========================================================

    async def start(
        self,
        update,
        context,
    ):
        if not await self._guard(update):
            return

        await self._menu_reply(
            update,
            "🤖 لوحة تحكم TASI KSA\n\n"
            "📊 اختر الوظيفة من الأزرار أسفل المحادثة.\n"
            "⚠️ Paper Trading فقط.\n\n"
            "الأوامر القديمة /signal وغيرها ما زالت تعمل احتياطًا.",
        )

    async def help(
        self,
        update,
        context,
    ):
        await self.start(
            update,
            context,
        )

    # =========================================================
    # SIGNAL COMMAND
    # =========================================================

    async def signal_command(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            "🔎 بدأ الفحص اليدوي للأسهم النشطة...\n"
            "لن تُنشأ صفقة إلا إذا اجتازت جميع الشروط.",
        )

        try:

            result = await self.service.scan_once(
                source="telegram_private"
            )

            pending = self.service.pending_signal()
            if pending:
                await self.send_admin_signal_preview(pending)
                await self._safe_reply(
                    update,
                    result + "\n\nاختر ✅ إرسال الصفقة أو ❌ إلغاء الصفقة من الأزرار.",
                )
            else:
                await self._safe_reply(update, result)

        except Exception as exc:

            print(
                "[telegram] /signal failed: "
                f"{exc!r}"
            )

            await self._safe_reply(
                update,
                "⚠️ تعذر إكمال الفحص حاليًا. "
                "راجع Render Logs.",
            )

    # =========================================================
    # MARKET
    # =========================================================

    async def market(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        try:

            text = await self.service.market_text()

            await self._safe_reply(
                update,
                text,
            )

        except Exception as exc:

            print(
                "[telegram] /market failed: "
                f"{exc!r}"
            )

            await self._safe_reply(
                update,
                "⚠️ تعذر قراءة حالة السوق.",
            )

    # =========================================================
    # OPEN TRADES
    # =========================================================

    async def open_trades(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            self.service.open_trades_text(),
        )

    # =========================================================
    # PERFORMANCE
    # =========================================================

    async def performance(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            self.service.performance_text(),
        )

    # =========================================================
    # DAILY / WEEKLY REPORT COMMANDS
    # =========================================================

    async def daily_report_command(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        try:
            await self._safe_reply(update, "📊 جاري إنشاء التقرير اليومي...")
            result = await self.service.daily_report(send=False, private=True)
            await self._safe_reply(update, result)
        except Exception as exc:
            print(f"[telegram] daily report failed: {exc!r}")
            await self._safe_reply(update, "⚠️ تعذر إنشاء التقرير اليومي حاليًا.")

    async def report_command(
        self,
        update,
        context,
    ):
        """
        /report من الخاص:

        1) ينشئ التقرير.
        2) يرسل الصورة للمشرف في الخاص.
        3) لا يرسل التقرير اليدوي للقروب أو القناة.
        """

        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        try:

            await self._safe_reply(
                update,
                "📊 جاري إنشاء التقرير الأسبوعي..."
            )

            result = await self.service.weekly_report(
                send=False,
                private=True,
            )

            await self._safe_reply(
                update,
                result,
            )

        except Exception as exc:

            print(
                "[telegram] /report failed: "
                f"{exc!r}"
            )

            await self._safe_reply(
                update,
                "⚠️ تعذر إنشاء أو إرسال التقرير حاليًا.",
            )

    # =========================================================
    # STATUS
    # =========================================================

    async def status(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            self.service.status_text(),
        )

    # =========================================================
    # HEALTH
    # =========================================================

    async def health(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        try:

            text = await self.service.health_text()

            await self._safe_reply(
                update,
                text,
            )

        except Exception as exc:

            print(
                "[telegram] /health failed: "
                f"{exc!r}"
            )

            await self._safe_reply(
                update,
                "⚠️ تعذر قراءة صحة النظام.",
            )


    # =========================================================
    # TASILAB DIAGNOSTIC
    # =========================================================

    async def test_tasilab(self, update, context):
        """Lightweight private-admin Tasilab diagnostic.

        Uses only three requests by default:
        - /v1/auth/me
        - /v1/market/status
        - /v1/market/quote/1120

        It intentionally skips bulk quotes to keep API consumption low.
        """
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        provider = getattr(self.service, "p", None)
        tasilab = getattr(provider, "tasilab", None)

        if tasilab is None or not hasattr(tasilab, "diagnose"):
            await self._safe_reply(
                update,
                "⚠️ تشخيص Tasilab غير متاح في هذه النسخة.",
            )
            return

        await self._safe_reply(
            update,
            "🧪 جاري اختبار Tasilab بثلاثة طلبات خفيفة...",
        )

        try:
            report = await tasilab.diagnose(
                "1120",
                include_bulk=False,
            )

            classification = str(
                report.get("classification", "UNKNOWN")
            )
            checks = report.get("checks", {}) or {}

            labels = {
                "auth": "المصادقة",
                "market_status": "حالة السوق",
                "single_quote": "سعر 1120",
            }

            lines = [
                "🧪 تشخيص Tasilab",
                f"النتيجة: {classification}",
                "",
            ]

            for key in ("auth", "market_status", "single_quote"):
                item = checks.get(key, {}) or {}
                status = item.get("status")
                latency = item.get("latency_ms")
                ok = bool(item.get("ok"))
                icon = "✅" if ok else "❌"
                status_text = str(status) if status is not None else "NETWORK"
                latency_text = (
                    f"{latency}ms" if latency is not None else "—"
                )
                lines.append(
                    f"{icon} {labels[key]}: HTTP {status_text} | {latency_text}"
                )

            # Show operational hints without exposing credentials or long HTML.
            failed = [
                item for item in checks.values()
                if isinstance(item, dict) and not item.get("ok")
            ]
            if failed:
                retry_after = next(
                    (str(x.get("retry_after")) for x in failed if x.get("retry_after")),
                    "",
                )
                cloudflare = any(bool(x.get("cloudflare")) for x in failed)
                if retry_after:
                    lines.append(f"⏳ Retry-After: {retry_after}s")
                if cloudflare:
                    lines.append("☁️ الخطأ مر عبر Cloudflare")

            meanings = {
                "HEALTHY": "✅ Tasilab يعمل حاليًا.",
                "AUTH_ERROR": "🔑 مشكلة في API Key أو صلاحية المصادقة.",
                "RATE_LIMIT": "⏳ تم الوصول إلى Rate Limit.",
                "PROVIDER_OR_UPSTREAM_5XX": "🌐 عطل 5xx من Tasilab أو المزود الخلفي.",
                "QUOTE_ENDPOINT_5XX": "🌐 Endpoint الأسعار يعاني خطأ 5xx.",
                "NETWORK_OR_TIMEOUT": "📡 تعذر الاتصال أو انتهت المهلة.",
                "ENDPOINT_OR_PARAMETER_ERROR": "⚙️ مشكلة endpoint أو parameters.",
                "DEGRADED_UNKNOWN": "⚠️ الخدمة تعمل جزئيًا وتحتاج مراجعة اللوق.",
            }
            lines.extend(["", meanings.get(classification, "⚠️ نتيجة غير معروفة.")])

            await self._safe_reply(update, "\n".join(lines))

        except Exception as exc:
            print("[telegram] /test_tasilab failed: " f"{exc!r}")
            await self._safe_reply(
                update,
                "⚠️ تعذر إكمال اختبار Tasilab. راجع Render Logs.",
            )

    # =========================================================
    # SETTINGS
    # =========================================================

    async def settings(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            self.service.settings_text(),
        )

    # =========================================================
    # RISK
    # =========================================================

    async def risk(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            self.service.risk_text(),
        )

    # =========================================================
    # PAUSE
    # =========================================================

    async def pause(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        if not await self._admin_only(update):

            await self._safe_reply(
                update,
                "🔒 أمر /pause متاح للمشرف فقط.",
            )

            return

        self.service.set_paused(
            True
        )

        await self._safe_reply(
            update,
            "⏸️ تم إيقاف إنشاء الإشارات الجديدة.\n"
            "الصفقات المفتوحة تستمر في المتابعة.",
        )

    # =========================================================
    # RESUME
    # =========================================================

    async def resume(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        if not await self._admin_only(update):

            await self._safe_reply(
                update,
                "🔒 أمر /resume متاح للمشرف فقط.",
            )

            return

        self.service.set_paused(
            False
        )

        await self._safe_reply(
            update,
            "▶️ تم استئناف إنشاء الإشارات الجديدة.",
        )

    # =========================================================
    # SIGNAL CONFIRMATION
    # =========================================================

    async def confirm_signal(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        ok, text = await self.service.confirm_pending_signal()
        await update.effective_message.reply_text(
            text,
            reply_markup=self._admin_menu(),
        )

    async def cancel_signal(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        cancelled = self.service.cancel_pending_signal()
        text = (
            "❌ تم إلغاء الصفقة. لم تُنشر ولم تُسجل ضمن Paper Trading."
            if cancelled
            else "ℹ️ لا توجد صفقة معلقة لإلغائها."
        )
        await update.effective_message.reply_text(text, reply_markup=self._admin_menu())

    # =========================================================
    # PRIVATE DISPLAY TESTS (NO MARKET API)
    # =========================================================

    async def tests_menu(self, update, context):
        if not await self._guard(update):
            return
        await update.effective_message.reply_text(
            "🧪 قائمة اختبارات العرض\n\nكل الاختبارات خاصة ولا تستدعي بيانات السوق.",
            reply_markup=self._tests_menu(),
        )

    async def back_main_menu(self, update, context):
        if not await self._guard(update):
            return
        if self.service and self.service.pending_signal():
            self.service.cancel_pending_signal()
        await self._menu_reply(update, "🤖 رجعت للقائمة الرئيسية.")

    async def test_trade_display(self, update, context):
        if not await self._guard(update):
            return
        admin_id = int(self.s.telegram_admin_user_id)
        text = (
            "🚨 اختبار عرض صفقة — خاص فقط\n\n"
            "السهم: أرامكو\nالرمز: 2222\n"
            "💰 الدخول: 30.50 ريال\n"
            "🎯 TP1: 31.20 | TP2: 32.10 | TP3: 33.20\n"
            "🛑 SL: 28.90\n\n"
            "⚠️ هذه رسالة اختبار واجهة فقط ولا تُسجل كصفقة."
        )
        with open(str(getattr(self.s, "trade_card_image", "app/assets/telegram/trade_card.png")), "rb") as fh:
            msg = await self.signal.send_photo(chat_id=admin_id, photo=fh, caption=text)
        self._last_test_trade_message_id = int(msg.message_id)
        await self._safe_reply(update, "✅ تم إرسال اختبار الصفقة في الخاص.")

    async def test_profit_display(self, update, context):
        if not await self._guard(update):
            return
        admin_id = int(self.s.telegram_admin_user_id)
        if not self._last_test_trade_message_id:
            await self.test_trade_display(update, context)
        text = (
            "🟢 اختبار تحديث أرباح — خاص فقط\n\n"
            "أرامكو — 2222\n"
            "الدخول: 30.50 ريال\nالسعر الحالي: 33.50 ريال\n"
            "الحركة: +3.00 ريال\n"
            "⚠️ اختبار واجهة فقط."
        )
        with open(str(getattr(self.s, "profit_update_image", "app/assets/telegram/profit_update.png")), "rb") as fh:
            await self.signal.send_photo(
                chat_id=admin_id,
                photo=fh,
                caption=text,
                reply_to_message_id=self._last_test_trade_message_id,
                allow_sending_without_reply=True,
            )
        await self._safe_reply(update, "✅ تم إرسال تحديث الأرباح كـ Reply على اختبار الصفقة.")

    async def test_daily_report_display(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self.service.daily_report(send=False, private=True)
        await self._safe_reply(update, "✅ تم استعراض التقرير اليومي في الخاص.")

    async def test_weekly_report_display(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self.service.weekly_report(send=False, private=True)
        await self._safe_reply(update, "✅ تم استعراض التقرير الأسبوعي في الخاص.")

    # =========================================================
    # PRIVATE MENU BUTTONS
    # =========================================================

    async def menu_button(self, update, context):
        """Route Arabic reply-keyboard buttons to the existing command logic."""
        if not await self._guard(update):
            return

        text = (update.effective_message.text or "").strip()
        routes = {
            "🔎 فحص فرصة": self.signal_command,
            "✅ إرسال الصفقة": self.confirm_signal,
            "❌ إلغاء الصفقة": self.cancel_signal,
            "📈 حالة السوق": self.market,
            "📂 الصفقات المفتوحة": self.open_trades,
            "📊 الأداء": self.performance,
            "🧾 تقرير يومي": self.daily_report_command,
            "📅 تقرير أسبوعي": self.report_command,
            "🩺 صحة النظام": self.health,
            "⚙️ الإعدادات": self.settings,
            "🛡️ المخاطر": self.risk,
            "🧪 قائمة الاختبارات": self.tests_menu,
            "🧪 اختبار Tasilab": self.test_tasilab,
            "📡 حالة النظام": self.status,
            "🧪 اختبار صفقة": self.test_trade_display,
            "🧪 اختبار تحديث أرباح": self.test_profit_display,
            "🧪 اختبار تقرير يومي": self.test_daily_report_display,
            "🧪 اختبار تقرير أسبوعي": self.test_weekly_report_display,
            "⬅️ رجوع للقائمة الرئيسية": self.back_main_menu,
            "⏸️ إيقاف الإشارات": self.pause,
            "▶️ استئناف الإشارات": self.resume,
            "ℹ️ المساعدة": self.help,
            "👤 معرفي": self.myid,
        }

        callback = routes.get(text)
        if callback is not None:
            await callback(update, context)
            return

        # Private admin free text is ignored to keep the control chat clean.
        # The persistent keyboard remains available under the input field.

    # =========================================================
    # ERROR HANDLER
    # =========================================================

    async def error(
        self,
        update,
        context,
    ):
        print(
            "[telegram] handler error: "
            f"{context.error!r}"
        )

    # =========================================================
    # HANDLERS
    # =========================================================

    def _add_handlers(self):

        handlers = {
            "start": self.start,
            "help": self.help,
            "signal": self.signal_command,
            "market": self.market,
            "open": self.open_trades,
            "performance": self.performance,
            "daily_report": self.daily_report_command,
            "report": self.report_command,
            "status": self.status,
            "health": self.health,
            "test_tasilab": self.test_tasilab,
            "test_trade": self.test_trade_display,
            "test_profit": self.test_profit_display,
            "test_daily_report": self.test_daily_report_display,
            "test_weekly_report": self.test_weekly_report_display,
            "settings": self.settings,
            "risk": self.risk,
            "pause": self.pause,
            "resume": self.resume,
            "myid": self.myid,
        }

        for name, callback in handlers.items():

            self.application.add_handler(
                CommandHandler(
                    name,
                    callback,
                )
            )

        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.menu_button,
            )
        )

        self.application.add_error_handler(
            self.error
        )

    # =========================================================
    # START TELEGRAM
    # =========================================================

    async def start_commands(self):

        if self.application is not None:
            return

        self.application = (
            Application.builder()
            .token(
                self.s.signal_bot_token
            )
            .build()
        )

        self._add_handlers()

        await self.application.initialize()
        await self.application.start()

        # -----------------------------------------------------
        # WEBHOOK
        # -----------------------------------------------------

        if self.mode == "webhook":

            base_url = os.getenv(
                "RENDER_EXTERNAL_URL",
                "",
            ).rstrip("/")

            if not base_url:

                raise RuntimeError(
                    "Webhook mode requires "
                    "RENDER_EXTERNAL_URL"
                )

            webhook_url = (
                f"{base_url}/telegram/webhook"
            )

            await self.application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=[
                    "message",
                ],
                drop_pending_updates=True,
                secret_token=self.webhook_secret,
            )

            print(
                "[telegram] webhook started: "
                f"{webhook_url}"
            )

        # -----------------------------------------------------
        # POLLING LOCAL
        # -----------------------------------------------------

        else:

            await self.application.bot.delete_webhook(
                drop_pending_updates=True
            )

            if self.application.updater is None:

                raise RuntimeError(
                    "Telegram updater is unavailable"
                )

            await self.application.updater.start_polling(
                allowed_updates=[
                    "message",
                ],
                drop_pending_updates=True,
            )

            print(
                "[telegram] command polling started"
            )

    # =========================================================
    # WEBHOOK PROCESSING
    # =========================================================

    async def process_webhook(
        self,
        payload,
        received_secret,
    ):

        if (
            self.mode != "webhook"
            or self.application is None
        ):
            return False

        if (
            not received_secret
            or not hmac.compare_digest(
                received_secret,
                self.webhook_secret,
            )
        ):
            return False

        update = Update.de_json(
            payload,
            self.application.bot,
        )

        await self.application.update_queue.put(
            update
        )

        return True

    # =========================================================
    # STOP
    # =========================================================

    async def stop_commands(self):

        if self.application is None:
            return

        try:

            if (
                self.application.updater
                and self.application.updater.running
            ):
                await self.application.updater.stop()

            if self.application.running:
                await self.application.stop()

            await self.application.shutdown()

        finally:
            self.application = None
