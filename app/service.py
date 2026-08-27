import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.data.universe import normalize_universe
from app.market.regime import classify_tasi, tasi_context
from app.scanner.screener import fast_score
from app.strategy.analyzer import assess_intraday
from app.signal_engine.engine import SignalEngine
from app.indicators.technical import resample_ohlcv
from app.telegram.messages import (
    profit_message,
    signal_message,
    tp_message,
)
from app.database.json_store import JsonStore
from app.trades.manager import TradeManager


class TradingService:
    """
    Core service.

    - New signals are MANUAL only via /signal.
    - Scheduler NEVER creates a new signal.
    - Scheduler only:
        * monitors open trades
        * sends TP / SL events
        * sends periodic price updates
        * sends market-close notification
        * sends scheduled weekly report
    """

    def __init__(
        self,
        settings,
        provider,
        bots,
        historical_provider=None,
    ):
        self.s = settings
        self.p = provider
        self.h = historical_provider
        self.b = bots

        self.store = JsonStore(
            settings.state_dir
        )

        self.trade_manager = TradeManager(
            self.store,
            settings,
        )

        # Bootstrap the universe locally when the router has bundled/runtime
        # symbols. This avoids spending 3+ SAHMK company-pagination requests
        # simply because Render restarted before a manual /signal scan.
        cached_companies = (
            provider.cached_companies()
            if hasattr(provider, "cached_companies")
            else []
        )
        self.universe = normalize_universe(cached_companies)

        self.last_refresh = self._utc_now() if self.universe else None
        self.last_scan = None
        self.last_monitor = None

        self.last_market_summary = None
        self.last_market_summary_at = None

        self.scan_cursor = 0
        self.monitor_cursor = 0

        self.scan_lock = asyncio.Lock()
        self.monitor_lock = asyncio.Lock()

        self.last_report_key = None
        self.last_daily_report_key = None
        self.last_market_close_key = None

        self.tz = ZoneInfo(
            self.s.timezone
        )

        self.b.attach_service(self)

    # =========================================================
    # TIME
    # =========================================================

    def _utc_now(self):
        return datetime.now(
            timezone.utc
        )

    def _local_now(self):
        return self._utc_now().astimezone(
            self.tz
        )

    @staticmethod
    def _minutes(clock_text):
        hour, minute = str(
            clock_text
        ).split(":", 1)

        return (
            int(hour) * 60
            + int(minute)
        )

    @staticmethod
    def _parse_datetime(value):
        if not value:
            return None

        if isinstance(
            value,
            datetime,
        ):
            dt = value

        else:
            try:
                dt = datetime.fromisoformat(
                    str(value).replace(
                        "Z",
                        "+00:00",
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                return None

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    # =========================================================
    # MARKET HOURS
    # =========================================================

    def market_is_open(self):
        local = self._local_now()

        # Saudi Exchange:
        # Sunday -> Thursday
        if local.weekday() in (
            4,
            5,
        ):
            return False

        minute = (
            local.hour * 60
            + local.minute
        )

        return (
            self._minutes(
                self.s.market_open
            )
            <= minute
            < self._minutes(
                self.s.market_close
            )
        )

    # =========================================================
    # QUOTE FRESHNESS
    # =========================================================

    def _quote_freshness(self, quote):
        if quote is None:
            return False, "missing_quote", None
        if getattr(quote, "price", 0) <= 0:
            return False, "invalid_price", None
        if getattr(quote, "updated_at", None) is None:
            return False, "missing_timestamp", None

        updated_at = quote.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        age = (self._utc_now() - updated_at).total_seconds() / 60.0
        if age < -5:
            return False, "future_timestamp", age
        if age > self.s.data_max_delay_minutes:
            return False, "stale", age
        return True, "ok", age

    def _fresh_quote(self, quote):
        return self._quote_freshness(quote)[0]

    # =========================================================
    # UNIVERSE
    # =========================================================

    async def refresh(self):
        self.universe = normalize_universe(
            await self.p.companies(
                "TASI"
            )
        )

        self.last_refresh = (
            self._utc_now()
        )

        self.scan_cursor = (
            min(
                self.scan_cursor,
                max(
                    0,
                    len(self.universe) - 1,
                ),
            )
            if self.universe
            else 0
        )

        state = self.store.state()

        state["meta"][
            "last_universe_refresh"
        ] = self.last_refresh.isoformat()

        state["meta"][
            "universe_size"
        ] = len(self.universe)

        self.store.save_state(
            state
        )

        print(
            f"[universe] "
            f"{len(self.universe)} companies"
        )

    async def _ensure_universe(self):
        if (
            self.universe
            and self.last_refresh
        ):
            age = (
                self._utc_now()
                - self.last_refresh
            ).total_seconds()

            if (
                age
                <= self.s.universe_refresh_seconds
            ):
                return

        try:
            await self.refresh()

        except Exception as exc:
            print(
                "[universe] refresh failed, "
                "continuing without metadata: "
                f"{exc}"
            )

    # =========================================================
    # STATE
    # =========================================================

    def is_paused(self):
        return bool(
            self.store.state().get(
                "paused",
                False,
            )
        )

    def set_paused(
        self,
        paused,
    ):
        state = self.store.state()

        state["paused"] = bool(
            paused
        )

        state["meta"][
            "paused_at"
        ] = self._utc_now().isoformat()

        self.store.save_state(
            state
        )

    def can_send(self):
        state = self.store.state()

        today = (
            self._local_now()
            .date()
            .isoformat()
        )

        return (
            not state.get(
                "paused",
                False,
            )
            and len(
                state["open_trades"]
            )
            < self.s.max_open_trades
            and state[
                "daily_signals"
            ].get(
                today,
                0,
            )
            < self.s.max_daily_signals
            and self.s.paper_mode
        )

    # =========================================================
    # PENDING SIGNAL CONFIRMATION
    # =========================================================

    def _clear_pending_signal(self):
        state = self.store.state()
        state["pending_signal"] = None
        self.store.save_state(state)

    def pending_signal(self):
        """Return a non-expired private preview without calling any market API."""
        state = self.store.state()
        pending = state.get("pending_signal")
        if not pending:
            return None

        expires_at = self._parse_datetime(pending.get("expires_at"))
        if expires_at is None or self._utc_now() >= expires_at:
            state["pending_signal"] = None
            self.store.save_state(state)
            return None

        signal = pending.get("signal")
        return dict(signal) if isinstance(signal, dict) else None

    def cancel_pending_signal(self):
        had_pending = self.pending_signal() is not None
        self._clear_pending_signal()
        return had_pending

    def _stage_pending_signal(self, signal):
        minutes = max(1, int(getattr(self.s, "signal_confirmation_expiry_minutes", 5)))
        now = self._utc_now()
        state = self.store.state()
        state["pending_signal"] = {
            "signal": signal.to_dict(),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=minutes)).isoformat(),
        }
        self.store.save_state(state)

    async def confirm_pending_signal(self):
        """Publish the already-scanned setup. This performs zero market API calls."""
        signal = self.pending_signal()
        if not signal:
            return False, "⌛ لا توجد صفقة معلقة صالحة. أعد فحص الفرصة من القائمة."

        if not self.can_send():
            return False, "ℹ️ تعذر الإرسال: النظام متوقف أو تم بلوغ حد الصفقات/الإشارات."

        if not self.trade_manager.add(signal):
            self._clear_pending_signal()
            return False, "⚠️ تعذر تسجيل الصفقة؛ قد تكون هناك صفقة مفتوحة لنفس السهم أو تم بلوغ الحد."

        state = self.store.state()
        day = self._local_now().date().isoformat()
        state["daily_signals"][day] = state["daily_signals"].get(day, 0) + 1
        state["pending_signal"] = None
        self.store.save_state(state)

        image_path = str(getattr(self.s, "trade_card_image", "app/assets/telegram/trade_card.png"))
        signal_ids = await self.b.send_signal(
            signal_message(signal),
            image_path=image_path,
            trade=signal,
        )
        if not signal_ids:
            # No public destination accepted the signal. Roll back the paper trade
            # and daily count so a Telegram outage cannot create a hidden trade.
            self.trade_manager.remove_open(signal["symbol"])
            state = self.store.state()
            state["daily_signals"][day] = max(0, state["daily_signals"].get(day, 1) - 1)
            self.store.save_state(state)
            return False, "⚠️ فشل نشر الصفقة للقروب/القناة؛ لم تُسجل كصفقة مفتوحة."

        self.trade_manager.set_signal_message_ids(signal["symbol"], signal_ids)

        print(f"[signal] confirmed/sent {signal['symbol']} strategy={signal.get('strategy', '—')}")
        return True, (
            "✅ تم تأكيد ونشر الصفقة الورقية.\n"
            f"{signal.get('name', '—')} ({signal.get('symbol', '—')})\n"
            f"⭐ Score: {float(signal.get('score', 0)):.1f}/100"
        )

    # =========================================================
    # CURSOR
    # =========================================================

    def _next_batch(
        self,
        size,
        cursor_name,
    ):
        if not self.universe:
            return []

        total = len(
            self.universe
        )

        size = min(
            max(
                1,
                int(size),
            ),
            total,
        )

        cursor = getattr(
            self,
            cursor_name,
        )

        end = (
            cursor + size
        )

        if end <= total:

            batch = self.universe[
                cursor:end
            ]

        else:

            batch = (
                self.universe[cursor:]
                + self.universe[
                    : end - total
                ]
            )

        setattr(
            self,
            cursor_name,
            end % total,
        )

        return batch

    # =========================================================
    # MARKET
    # =========================================================

    async def _market(
        self,
        force=False,
    ):
        now = self._utc_now()

        if (
            not force
            and self.last_market_summary
            is not None
            and self.last_market_summary_at
        ):
            age = (
                now
                - self.last_market_summary_at
            ).total_seconds()

            if (
                age
                < self.s.market_cache_seconds
            ):
                return (
                    self.last_market_summary
                )

        try:
            data = await self.p.market_summary()

            self.last_market_summary = data
            self.last_market_summary_at = now

            return data

        except Exception as exc:
            print(
                "[market] summary failed: "
                f"{exc}"
            )

            return None

    # =========================================================
    # HISTORICAL HELPERS
    # =========================================================

    @staticmethod
    def _rows_to_df(
        payload,
    ):
        if not isinstance(
            payload,
            dict,
        ):
            return None

        rows = payload.get(
            "data",
            payload.get(
                "results",
                payload.get(
                    "historical",
                    [],
                ),
            ),
        )

        if (
            not isinstance(
                rows,
                list,
            )
            or len(rows) < 60
        ):
            return None

        df = pd.DataFrame(
            rows
        )

        rename_map = {}

        for column in df.columns:
            key = str(
                column
            ).lower()

            if key in (
                "o",
                "open",
            ):
                rename_map[
                    column
                ] = "open"

            elif key in (
                "h",
                "high",
            ):
                rename_map[
                    column
                ] = "high"

            elif key in (
                "l",
                "low",
            ):
                rename_map[
                    column
                ] = "low"

            elif key in (
                "c",
                "close",
            ):
                rename_map[
                    column
                ] = "close"

            elif key in (
                "v",
                "volume",
            ):
                rename_map[
                    column
                ] = "volume"

        df = df.rename(
            columns=rename_map
        )

        required = {
            "open",
            "high",
            "low",
            "close",
            "volume",
        }

        return (
            df
            if required.issubset(
                df.columns
            )
            else None
        )

    # =========================================================
    # MANUAL SIGNAL
    # =========================================================

    async def scan_once(
        self,
        source="telegram",
    ):
        if self.scan_lock.locked():
            return (
                "⏳ يوجد فحص يدوي "
                "جارٍ حاليًا."
            )

        async with self.scan_lock:

            self.last_scan = (
                self._utc_now()
            )

            state = self.store.state()

            state["meta"][
                "last_scan"
            ] = self.last_scan.isoformat()

            state["meta"][
                "last_scan_source"
            ] = source

            self.store.save_state(
                state
            )

            # -------------------------------------------------
            # SAFETY
            # -------------------------------------------------

            if self.is_paused():

                return (
                    "⏸️ النظام متوقف مؤقتًا. "
                    "استخدم /resume أولًا."
                )

            if not self.s.paper_mode:

                return (
                    "🛑 PAPER_MODE غير مفعّل؛ "
                    "تم منع إنشاء الصفقة."
                )

            if not self.can_send():

                return (
                    "ℹ️ تم بلوغ حد الصفقات "
                    "المفتوحة أو الإشارات اليومية."
                )

            if (
                not self.s.allow_off_hours_scan
                and not self.market_is_open()
            ):

                return (
                    "🌙 السوق السعودي مغلق حاليًا.\n"
                    f"وقت الفحص المسموح: "
                    f"{self.s.market_open}–"
                    f"{self.s.market_close} "
                    "بتوقيت الرياض، "
                    "الأحد–الخميس.\n"
                    "لن يتم إنشاء إشارة "
                    "من أسعار إغلاق قديمة."
                )

            # Saudi-market quality window: avoid the noisy opening phase and
            # stop new entries before the closing auction. This affects only
            # signal creation; trade monitoring/reporting remain unchanged.
            if not self.s.allow_off_hours_scan:
                local = self._local_now()
                minute = local.hour * 60 + local.minute
                start_text = getattr(self.s, "signal_window_start", "10:30")
                end_text = getattr(self.s, "signal_window_end", "14:30")
                start = self._minutes(start_text)
                end = self._minutes(end_text)
                if not (start <= minute <= end):
                    return (
                        "🛡️ فلتر جودة السوق السعودي مفعل.\n"
                        f"نافذة إنشاء الإشارات: {start_text}–"
                        f"{end_text} بتوقيت الرياض.\n"
                        "خارجها يجمع النظام/يراقب فقط ولا يطارد حركة الافتتاح أو الإغلاق."
                    )

            # A new explicit scan supersedes any older private preview.
            self._clear_pending_signal()

            # -------------------------------------------------
            # UNIVERSE
            # -------------------------------------------------

            await self._ensure_universe()

            universe_by_symbol = {
                str(
                    item.get(
                        "symbol",
                        "",
                    )
                ).strip(): item
                for item in self.universe
                if item.get(
                    "symbol"
                )
            }

            # -------------------------------------------------
            # MARKET REGIME
            # -------------------------------------------------

            market_data = (
                await self._market()
            )

            market_ctx = tasi_context(market_data)
            regime = market_ctx["regime"]

            screen_limit = min(
                max(
                    1,
                    int(
                        self.s.manual_quotes_per_signal
                    ),
                ),
                50,
            )

            detail_limit = min(
                max(
                    1,
                    int(
                        self.s.detail_quotes_per_signal
                    ),
                ),
                10,
            )

            # -------------------------------------------------
            # TOP VOLUME SCREEN
            # -------------------------------------------------

            try:

                screening_quotes = (
                    await self.p.top_volume_quotes(
                        screen_limit,
                        "TASI",
                    )
                )

                selection_source = (
                    "top_volume"
                )

            except Exception as exc:

                print(
                    "[manual-scan] "
                    "top-volume failed: "
                    f"{exc}"
                )

                screening_quotes = []

                selection_source = (
                    "fallback"
                )

            # -------------------------------------------------
            # FALLBACK
            # -------------------------------------------------

            if not screening_quotes:

                fallback_items = (
                    self._next_batch(
                        detail_limit,
                        "scan_cursor",
                    )
                )

                fallback_symbols = [
                    str(
                        x.get(
                            "symbol",
                            "",
                        )
                    ).strip()
                    for x in fallback_items
                    if x.get(
                        "symbol"
                    )
                ]

                details = await self.p.quotes(
                    fallback_symbols
                )

                screening_quotes = list(
                    details.values()
                )

            # -------------------------------------------------
            # FRESH DATA
            # -------------------------------------------------

            fresh_screening = []
            freshness_rejected = {}
            for q in screening_quotes:
                ok, reason, age = self._quote_freshness(q)
                if ok:
                    fresh_screening.append(q)
                else:
                    freshness_rejected[reason] = freshness_rejected.get(reason, 0) + 1
                    symbol = getattr(q, "symbol", "?") if q is not None else "?"
                    age_text = f" age={age:.1f}m" if age is not None else ""
                    print(f"[freshness] reject {symbol}: {reason}{age_text}")

            provider_name = (
                self.p.active_provider()
                if hasattr(self.p, "active_provider")
                else "unknown"
            )

            print(
                "[manual-scan] "
                f"source={source} "
                f"provider={provider_name} "
                f"selection={selection_source} "
                f"screened={len(fresh_screening)}/{len(screening_quotes)} "
                f"universe={len(self.universe)} "
                f"rejected={freshness_rejected}"
            )

            # -------------------------------------------------
            # FAST SCORE
            # -------------------------------------------------

            preliminary = []

            threshold = max(
                60.0,
                float(
                    self.s.min_score
                )
                - 15.0,
            )

            for quote in fresh_screening:

                try:

                    candidate = fast_score(
                        quote,
                        regime,
                    )

                    if (
                        candidate.score
                        >= threshold
                    ):
                        preliminary.append(
                            candidate
                        )

                except Exception as exc:

                    print(
                        "[score] "
                        f"{quote.symbol} "
                        f"failed: {exc}"
                    )

            preliminary.sort(
                key=lambda c: (
                    c.score,
                    c.quote.volume,
                    c.quote.value,
                ),
                reverse=True,
            )

            finalists = preliminary[
                :detail_limit
            ]

            if not finalists:

                return (
                    "🔎 اكتمل الفحص اليدوي.\n"
                    f"🎯 أسهم نشطة مستهدفة: "
                    f"{len(screening_quotes)}\n"
                    f"✅ بيانات حديثة صالحة: "
                    f"{len(fresh_screening)}\n"
                    "لم يظهر مرشح أولي يستحق "
                    "طلب تفاصيل إضافية."
                )

            # -------------------------------------------------
            # DETAILED QUOTES
            # -------------------------------------------------

            detailed_quotes = (
                await self.p.quotes(
                    [
                        c.quote.symbol
                        for c in finalists
                    ]
                )
            )

            print(
                "[manual-scan] detailed "
                "quotes returned "
                f"{len(detailed_quotes)}/"
                f"{len(finalists)}"
            )

            engine = SignalEngine(
                self.s,
                self.store.history(),
            )

            # -------------------------------------------------
            # PROFESSIONAL ANALYSIS
            # -------------------------------------------------

            for preliminary_candidate in finalists:

                symbol = (
                    preliminary_candidate
                    .quote
                    .symbol
                )

                quote = detailed_quotes.get(
                    symbol
                )

                if (
                    quote is None
                    or not self._fresh_quote(
                        quote
                    )
                ):
                    continue

                candidate = fast_score(
                    quote,
                    regime,
                )

                item = universe_by_symbol.get(
                    symbol,
                    {},
                )

                sector = item.get(
                    "sector",
                    "",
                )

                try:

                    if self.h is None:

                        print(
                            f"[analysis] {symbol}: "
                            "historical provider "
                            "unavailable"
                        )

                        continue

                    datasets = await self.h.datasets(
                        symbol
                    )

                    intraday_df = datasets.get(
                        "intraday"
                    )

                    daily_df = datasets.get(
                        "daily"
                    )

                    # Both the intraday entry frame and the daily context must
                    # be consistent with the current SAHMK quote. Daily validation
                    # remains loose enough for delayed research data; intraday is
                    # intentionally tighter because it drives the entry decision.
                    if not self.h.validate_against_quote(
                        daily_df, quote.price, self.s.historical_max_price_gap_pct
                    ):
                        print(f"[analysis] {symbol}: daily historical price validation failed")
                        continue

                    intraday_gap_limit = min(
                        float(self.s.historical_max_price_gap_pct),
                        float(getattr(self.s, "intraday_max_price_gap_pct", 4.0)),
                    )
                    if not self.h.validate_against_quote(
                        intraday_df, quote.price, intraday_gap_limit
                    ):
                        print(f"[analysis] {symbol}: intraday price validation failed")
                        continue

                    if (
                        intraday_df is None
                        or len(intraday_df) < self.s.intraday_min_bars
                        or daily_df is None
                        or len(daily_df) < self.s.swing_min_bars
                    ):
                        continue

                    hist_stamp = self.h.last_stamp(intraday_df)
                    if hist_stamp is None:
                        continue
                    hist_age = (self._utc_now() - hist_stamp.astimezone(timezone.utc)).total_seconds() / 60.0
                    max_hist_age = float(getattr(self.s, "historical_intraday_max_age_minutes", 45))
                    if hist_age < -5 or hist_age > max_hist_age:
                        print(f"[analysis] {symbol}: intraday historical stale age={hist_age:.1f}m")
                        continue

                    h1_df = resample_ohlcv(intraday_df, "60min")
                    # EMA20 on the higher frame needs enough completed history.
                    # Fail explicitly rather than letting a short/partial 60m
                    # series masquerade as a weak technical setup.
                    if h1_df is None or len(h1_df) < 25:
                        print(f"[analysis] {symbol}: insufficient 60m confirmation bars ({0 if h1_df is None else len(h1_df)})")
                        continue
                    assessment = assess_intraday(
                        intraday_df,
                        regime,
                        higher_tf_df=h1_df,
                        daily_df=daily_df,
                        market_context=market_ctx,
                    )
                    signal = None
                    assessments = [(assessment, hist_stamp)] if assessment is not None else []

                    for (assessment, hist_stamp) in assessments:

                        if getattr(assessment, "hard_rejects", None):
                            print(
                                f"[anti-fake] {symbol} rejected: "
                                + " | ".join(assessment.hard_rejects)
                            )
                            continue

                        if (
                            assessment.score
                            < self.s.min_score
                            or getattr(assessment, "grade", "B") not in {"A+", "A"}
                        ):
                            continue

                        signal = (
                            engine.build_assessment(
                                candidate,
                                regime,
                                sector,
                                assessment,
                                quote_updated_at=(
                                    quote.updated_at.isoformat()
                                    if quote.updated_at
                                    else ""
                                ),
                                historical_updated_at=(
                                    hist_stamp.isoformat()
                                    if hist_stamp
                                    else ""
                                ),
                            )
                        )

                        if signal:
                            break

                except Exception as exc:

                    print(
                        f"[analysis] {symbol} "
                        f"failed: {exc}"
                    )

                    continue

                if not signal:
                    continue

                # -------------------------------------------------
                # PRIVATE PREVIEW / MANUAL CONFIRMATION
                # -------------------------------------------------

                # Do NOT add the paper trade and do NOT publish yet.
                # Store the exact scan result for a short confirmation window.
                # Confirming later reuses this object and makes zero market API calls.
                self._stage_pending_signal(signal)

                print(
                    f"[signal] staged for admin confirmation "
                    f"{symbol} strategy={signal.strategy}"
                )

                return (
                    "🟡 تم اكتشاف فرصة مستوفية للشروط وبانتظار تأكيدك.\n"
                    f"{signal.name} ({signal.symbol})\n"
                    f"⭐ Score: {signal.score:.1f}/100\n"
                    f"🧭 نوع الصفقة: {signal.trade_type}\n"
                    f"📊 حالة الاحتمالية: {signal.probability_status}\n"
                    f"⏳ صلاحية التأكيد: {int(getattr(self.s, 'signal_confirmation_expiry_minutes', 5))} دقائق.\n"
                    "لم تُسجل أو تُنشر الصفقة بعد."
                )

            return (
                "🔎 اكتمل الفحص اليدوي.\n"
                f"🎯 الأسهم النشطة المفروزة: "
                f"{len(fresh_screening)}\n"
                f"🔬 المرشحون بتفاصيل كاملة: "
                f"{len(detailed_quotes)}\n"
                "لم توجد صفقة مستوفية "
                "لجميع شروط Paper Trading."
            )

    # =========================================================
    # PRICE UPDATE
    # =========================================================

    def _price_update_due(
        self,
        trade,
    ):
        """
        هل حان موعد نشر تحديث السعر؟

        أول تحديث لن يخرج قبل مرور المدة
        من وقت اكتشاف الصفقة.
        """

        interval = max(
            5,
            int(
                getattr(
                    self.s,
                    "trade_price_update_minutes",
                    30,
                )
            ),
        )

        anchor = (
            trade.get(
                "last_price_public_update_at"
            )
            or trade.get(
                "discovered_at"
            )
        )

        when = self._parse_datetime(
            anchor
        )

        if when is None:
            return True

        elapsed = (
            self._utc_now()
            - when
        ).total_seconds() / 60

        return (
            elapsed >= interval
        )

    def _price_update_status(
        self,
        trade,
        price,
    ):
        entry = float(
            trade["entry"]
        )

        tp1 = float(
            trade["tp1"]
        )

        tp2 = float(
            trade["tp2"]
        )

        tp3 = float(
            trade["tp3"]
        )

        sl = float(
            trade.get(
                "trailing_stop"
            )
            or trade["sl"]
        )

        if price >= tp3:
            return (
                "🎯 عند/فوق الهدف الثالث"
            )

        if price >= tp2:
            return (
                "🎯 بين الهدف الثاني "
                "والثالث"
            )

        if price >= tp1:
            return (
                "🎯 بين الهدف الأول "
                "والثاني"
            )

        if entry > 0:

            distance_tp1 = (
                abs(
                    tp1 - price
                )
                / entry
                * 100
            )

            distance_sl = (
                abs(
                    price - sl
                )
                / entry
                * 100
            )

            if (
                price < tp1
                and distance_tp1 <= 0.5
            ):
                return (
                    "🟡 قريب من الهدف الأول"
                )

            if (
                price > sl
                and distance_sl
                <= self.s.near_sl_warning_pct
            ):
                return (
                    "🟠 قريب من وقف الخسارة"
                )

        if price >= entry:
            return (
                "🟢 أعلى من سعر الدخول"
            )

        return (
            "🟠 أقل من سعر الدخول"
        )

    def _price_update_text(
        self,
        trade,
        quote,
    ):
        entry = float(
            trade["entry"]
        )

        price = float(
            quote.price
        )

        pct = (
            (
                price - entry
            )
            / entry
            * 100
            if entry
            else 0
        )

        local_time = (
            self._local_now()
            .strftime(
                "%H:%M"
            )
        )

        trade_type = (
            trade.get(
                "trade_type"
            )
            or "—"
        )

        return (
            "📊 تحديث صفقة مفتوحة\n\n"

            f"السهم: "
            f"{trade.get('name', '')}\n"

            f"الرمز: "
            f"{trade.get('symbol', '')}\n"

            f"🧭 نوع الصفقة: "
            f"{trade_type}\n\n"

            f"💰 سعر الدخول: "
            f"{entry:.2f}\n"

            f"📍 السعر الحالي: "
            f"{price:.2f}\n"

            f"📈 التغير من الدخول: "
            f"{pct:+.2f}%\n\n"

            f"🎯 الهدف الأول: "
            f"{float(trade['tp1']):.2f}\n"

            f"🎯 الهدف الثاني: "
            f"{float(trade['tp2']):.2f}\n"

            f"🎯 الهدف الثالث: "
            f"{float(trade['tp3']):.2f}\n"

            f"🛑 وقف الخسارة: "
            f"{float(trade.get('trailing_stop') or trade['sl']):.2f}\n\n"

            f"📌 الحالة: "
            f"{self._price_update_status(trade, price)}\n\n"

            f"🕒 آخر تحديث: "
            f"{local_time} بتوقيت الرياض\n"

            "📡 بيانات سهمك متأخرة حسب الباقة"
        )

    # =========================================================
    # TRADE MONITOR
    # =========================================================

    async def monitor_once(self):
        """
        Monitor open Paper Trades only.

        NEVER creates a new trade.
        """

        if (
            self.monitor_lock.locked()
            or not self.market_is_open()
        ):
            return

        async with self.monitor_lock:

            self.last_monitor = (
                self._utc_now()
            )

            state = (
                self.store.state()
            )

            if not state[
                "open_trades"
            ]:
                return

            trades = state[
                "open_trades"
            ]

            total = len(
                trades
            )

            batch_size = min(
                max(
                    1,
                    int(
                        self.s.trade_monitor_quotes_per_cycle
                    ),
                ),
                total,
            )

            start = (
                self.monitor_cursor
                % total
            )

            selected = [
                trades[
                    (start + i) % total
                ]
                for i in range(
                    batch_size
                )
            ]

            self.monitor_cursor = (
                start
                + len(selected)
            ) % total

            for trade in selected:

                symbol = trade[
                    "symbol"
                ]

                try:

                    quote = (
                        await self.p.quote(
                            symbol
                        )
                    )

                    if not self._fresh_quote(
                        quote
                    ):

                        print(
                            f"[monitor] {symbol}: "
                            "stale/missing timestamp"
                        )

                        continue

                    # -----------------------------------------
                    # UPDATE TRADE
                    # -----------------------------------------

                    updated, events = (
                        self.trade_manager.update(
                            symbol,
                            quote.price,
                        )
                    )

                    if not updated:
                        continue

                    # -----------------------------------------
                    # TP / SL EVENTS
                    # -----------------------------------------

                    for event in events:

                        if event == "CLOSE_TP3":

                            await self.b.send_profit(
                                tp_message(updated, "TP3", quote.price),
                                trade=updated,
                                image_path=str(getattr(self.s, "profit_update_image", "app/assets/telegram/profit_update.png")),
                            )

                        elif event == "SL":

                            await self.b.send_loss_for_trade(
                                updated,
                                quote.price,
                            )

                        elif event in {
                            "TP1",
                            "TP2",
                        }:

                            await self.b.send_profit(
                                tp_message(updated, event, quote.price),
                                trade=updated,
                                image_path=str(getattr(self.s, "profit_update_image", "app/assets/telegram/profit_update.png")),
                            )

                    # -----------------------------------------
                    # OPEN TRADE ONLY
                    # -----------------------------------------

                    if (
                        updated.get(
                            "status"
                        )
                        != "OPEN"
                    ):
                        continue

                    # -----------------------------------------
                    # TRAILING
                    # -----------------------------------------

                    self.trade_manager.apply_trailing(
                        updated,
                        quote.price,
                        atr=None,
                    )

                    # -----------------------------------------
                    # PROFIT %
                    # -----------------------------------------

                    entry = float(
                        updated[
                            "entry"
                        ]
                    )

                    pct = (
                        (
                            quote.price
                            - entry
                        )
                        / entry
                        * 100
                    )

                    sent = set(
                        updated.get(
                            "profit_alerts_sent",
                            [],
                        )
                    )

                    try:

                        thresholds = [
                            float(
                                x.strip()
                            )
                            for x in (
                                self.s
                                .profit_alert_thresholds
                                .split(",")
                            )
                            if x.strip()
                        ]

                    except ValueError:

                        thresholds = [
                            2,
                            5,
                            10,
                            15,
                            20,
                        ]

                    # -----------------------------------------
                    # PROFIT ALERTS
                    # -----------------------------------------

                    for threshold in thresholds:

                        if (
                            pct >= threshold
                            and threshold
                            not in sent
                        ):

                            await self.b.send_profit(
                                profit_message(updated, quote.price, quote.price - entry),
                                trade=updated,
                                image_path=str(getattr(self.s, "profit_update_image", "app/assets/telegram/profit_update.png")),
                            )

                            sent.add(
                                threshold
                            )

                    # -----------------------------------------
                    # NEAR STOP
                    # -----------------------------------------

                    stop = float(
                        updated.get(
                            "trailing_stop"
                        )
                        or updated[
                            "sl"
                        ]
                    )

                    distance_pct = (
                        abs(
                            quote.price
                            - stop
                        )
                        / entry
                        * 100
                    )

                    if (
                        quote.price > stop
                        and distance_pct
                        <= self.s.near_sl_warning_pct
                        and not updated.get(
                            "near_sl_warning_sent"
                        )
                    ):

                        await self.b.send_near_sl(
                            updated,
                            quote.price,
                        )

                        updated[
                            "near_sl_warning_sent"
                        ] = True

                    # -----------------------------------------
                    # PERIODIC PRICE UPDATE
                    # -----------------------------------------

                    if self._price_update_due(
                        updated
                    ):

                        await self.b.send_profit(
                            self._price_update_text(updated, quote),
                            trade=updated,
                        )

                        updated[
                            "last_price_public_update_at"
                        ] = (
                            self._utc_now()
                            .isoformat()
                        )

                        print(
                            "[monitor] periodic "
                            "price update sent "
                            f"{symbol}"
                        )

                    # -----------------------------------------
                    # SAVE MONITOR STATE
                    # -----------------------------------------

                    current = (
                        self.store.state()
                    )

                    for item in current[
                        "open_trades"
                    ]:

                        if (
                            item[
                                "symbol"
                            ]
                            == symbol
                        ):

                            item[
                                "profit_alerts_sent"
                            ] = sorted(
                                sent
                            )

                            item[
                                "near_sl_warning_sent"
                            ] = updated.get(
                                "near_sl_warning_sent",
                                False,
                            )

                            item[
                                "trailing_stop"
                            ] = updated.get(
                                "trailing_stop"
                            )

                            item[
                                "last_price_public_update_at"
                            ] = updated.get(
                                "last_price_public_update_at"
                            )

                    self.store.save_state(
                        current
                    )

                except Exception as exc:

                    print(
                        f"[monitor] {symbol} "
                        f"failed: {exc}"
                    )

    # =========================================================
    # SCHEDULED TASKS
    # =========================================================

    async def scheduled_tasks(self):

        await self.monitor_once()

        await (
            self._scheduled_market_close_message()
        )

        await self._scheduled_daily_report()

        await (
            self._scheduled_weekly_report()
        )

    # =========================================================
    # MARKET CLOSE
    # =========================================================

    async def _scheduled_market_close_message(
        self,
    ):
        local = (
            self._local_now()
        )

        if local.weekday() in (
            4,
            5,
        ):
            return

        key = (
            local.date()
            .isoformat()
        )

        if (
            key
            == self.last_market_close_key
        ):
            return

        current_minute = (
            local.hour * 60
            + local.minute
        )

        if (
            current_minute
            < self._minutes(
                self.s.market_close
            )
        ):
            return

        self.last_market_close_key = key

        await self.b.send_market_close(
            local.strftime(
                "%Y-%m-%d %H:%M %Z"
            )
        )

    # =========================================================
    # SCHEDULED DAILY REPORT
    # =========================================================

    async def _scheduled_daily_report(self):
        local = self._local_now()
        if local.weekday() in (4, 5) or not getattr(self.s, "daily_report_enabled", True):
            return
        key = local.date().isoformat()
        if key == self.last_daily_report_key:
            return
        target = int(getattr(self.s, "daily_report_hour", 15)) * 60 + int(getattr(self.s, "daily_report_minute", 5))
        now_min = local.hour * 60 + local.minute
        if now_min < target:
            return
        self.last_daily_report_key = key
        await self.daily_report(send=True, private=False)

    # =========================================================
    # SCHEDULED WEEKLY REPORT
    # =========================================================

    async def _scheduled_weekly_report(
        self,
    ):
        local = (
            self._local_now()
        )

        key = (
            local.date()
            .isoformat()
        )

        if (
            not self.s.weekly_report_enabled
            or local.weekday()
            != self.s.weekly_report_weekday
        ):
            return

        report_minute = (
            self.s.weekly_report_hour
            * 60
            + self.s.weekly_report_minute
        )

        current_minute = (
            local.hour * 60
            + local.minute
        )

        if (
            current_minute
            < report_minute
            or self.last_report_key
            == key
        ):
            return

        self.last_report_key = key

        # Automatic report:
        # GROUP + CHANNEL
        await self.weekly_report(
            send=True,
            private=False,
        )

    # =========================================================
    # MARKET TEXT
    # =========================================================

    async def market_text(self):

        data = await self._market()

        if not data:

            return (
                "⚠️ بيانات السوق "
                "غير متاحة حاليًا."
            )

        return (
            "📊 حالة السوق السعودي\n\n"

            f"TASI: "
            f"{data.get('value', data.get('index', '—'))}\n"

            f"التغير: "
            f"{data.get('change_percent', data.get('change_pct', '—'))}%\n"

            f"Market Regime: "
            f"{classify_tasi(data)}\n"

            f"الأسهم الصاعدة: "
            f"{data.get('advancers', data.get('advancing', '—'))}\n"

            f"الأسهم الهابطة: "
            f"{data.get('decliners', data.get('declining', '—'))}\n"

            f"قيمة التداول: "
            f"{data.get('trading_value', data.get('value_traded', '—'))}\n\n"

            "📡 المصدر: SAHMK delayed\n"

            "⚠️ لا يتم إنشاء إشارات "
            "تلقائية. استخدم /signal."
        )

    # =========================================================
    # OPEN TRADES
    # =========================================================

    def open_trades_text(self):

        trades = self.store.state()[
            "open_trades"
        ]

        if not trades:

            return (
                "📭 لا توجد صفقات "
                "مفتوحة حاليًا."
            )

        lines = [
            "📂 الصفقات المفتوحة",
            "",
        ]

        for trade in trades:

            lines.append(
                f"{trade['name']} "
                f"({trade['symbol']})\n"

                f"🧭 النوع: "
                f"{trade.get('trade_type', '—')}\n"

                f"دخول: "
                f"{float(trade['entry']):.2f} | "

                f"الحالي: "
                f"{float(trade.get('current_price', trade['entry'])):.2f}\n"

                f"SL: "
                f"{float(trade['sl']):.2f} | "

                f"TP1: "
                f"{float(trade['tp1']):.2f} | "

                f"TP2: "
                f"{float(trade['tp2']):.2f} | "

                f"TP3: "
                f"{float(trade['tp3']):.2f}"
            )

        return "\n\n".join(
            lines
        )

    # =========================================================
    # WEEKLY REPORT
    # =========================================================

    def _report_history(self, period):
        history = list(self.store.history())
        now_local = self._local_now()
        if period == "daily":
            target_date = now_local.date()
            selected = []
            for item in history:
                stamp = item.get("exit_time") or item.get("discovered_at")
                when = self._parse_datetime(stamp)
                if when and when.astimezone(self.tz).date() == target_date:
                    selected.append(item)
            return selected

        cutoff = self._utc_now() - timedelta(days=7)
        selected = []
        for item in history:
            stamp = item.get("exit_time") or item.get("discovered_at")
            when = self._parse_datetime(stamp)
            if when and when >= cutoff:
                selected.append(item)
        return selected

    def _report_text(self, period, history):
        local = self._local_now()
        wins = [x for x in history if x.get("result") == "WIN"]
        losses = [x for x in history if x.get("result") == "LOSS"]
        pending = len(self.store.state().get("open_trades", []))
        settled = len(wins) + len(losses)
        win_rate = (len(wins) / settled * 100.0) if settled else 0.0
        gross_win = sum(max(0.0, float(x.get("result_pct") or 0.0)) for x in history)
        gross_loss = abs(sum(min(0.0, float(x.get("result_pct") or 0.0)) for x in history))
        net = sum(float(x.get("result_pct") or 0.0) for x in history)

        if period == "daily":
            title = "اليومي"
            period_label = local.strftime("%d-%m-%Y")
            profit_label = "اليوم"
        else:
            title = "الأسبوعي"
            start_date = (local.date() - timedelta(days=6)).strftime("%d-%m")
            period_label = f"{start_date} – {local.strftime('%d-%m-%Y')}"
            profit_label = "الأسبوع"

        return (
            "✨ نتائج ALLUQMANU_TASI ✨\n"
            f"📊 تقرير تداول TASI {title}\n"
            f"▫️ {period_label} ▫️\n\n"
            f"✅ أرباح {profit_label}: +{gross_win:.2f}%\n"
            f"❌ خسائر {profit_label}: -{gross_loss:.2f}%\n"
            f"📈 صافي الأداء: {net:+.2f}%\n\n"
            "🎯 معيار نجاح الإشارة: بلوغ أهداف السعر المحددة للصفقة\n"
            f"✅ إشارات وصلت للهدف: {len(wins)}\n"
            f"🟢 الصفقات الناجحة: {len(wins)}\n"
            f"🔴 الصفقات الخاسرة: {len(losses)}\n"
            f"⏳ قيد الانتظار: {pending}\n"
            f"📊 نسبة النجاح: {win_rate:.1f}%\n\n"
            "📌 الأسهم: النجاح يُحتسب حسب الأهداف وإدارة الصفقة، والخسارة حسب إغلاق الصفقة ووقفها الفعلي.\n"
            "⚠️ Paper Trading فقط."
        )

    async def daily_report(self, send=True, private=False):
        history = self._report_history("daily")
        text = self._report_text("daily", history)
        image_path = str(getattr(self.s, "daily_report_image", "app/assets/telegram/daily_report.png"))
        if private:
            await self.b.send_admin_report(text=text, image_path=image_path)
            return "✅ تم إرسال التقرير اليومي التجريبي في الخاص."
        if send:
            await self.b.send_report(text=text, image_path=image_path)
            return "📊 تم إرسال التقرير اليومي إلى القروب والقناة."
        return text

    async def weekly_report(self, send=True, private=False):
        history = self._report_history("weekly")
        text = self._report_text("weekly", history)
        image_path = str(getattr(self.s, "weekly_report_image", "app/assets/telegram/weekly_report.png"))
        if private:
            await self.b.send_admin_report(text=text, image_path=image_path)
            return "✅ تم إرسال التقرير الأسبوعي التجريبي في الخاص."
        if send:
            await self.b.send_report(text=text, image_path=image_path)
            return "📊 تم إرسال التقرير الأسبوعي إلى القروب والقناة."
        return text

    # =========================================================
    # PERFORMANCE
    # =========================================================

    def performance_text(self):

        history = (
            self.store.history()
        )

        wins = [
            x
            for x in history
            if x.get(
                "result"
            )
            == "WIN"
        ]

        losses = [
            x
            for x in history
            if x.get(
                "result"
            )
            == "LOSS"
        ]

        closed = (
            len(wins)
            + len(losses)
        )

        win_rate = (
            len(wins)
            / closed
            * 100
            if closed
            else 0
        )

        avg = (
            sum(
                float(
                    x.get(
                        "result_pct"
                    )
                    or 0
                )
                for x in history
            )
            / len(history)
            if history
            else 0
        )

        gross_win = sum(
            max(
                0,
                float(
                    x.get(
                        "result_pct"
                    )
                    or 0
                ),
            )
            for x in history
        )

        gross_loss = abs(
            sum(
                min(
                    0,
                    float(
                        x.get(
                            "result_pct"
                        )
                        or 0
                    ),
                )
                for x in history
            )
        )

        pf = (
            gross_win
            / gross_loss
            if gross_loss
            else 0
        )

        return (
            "📈 أداء Paper Trading\n\n"

            f"الصفقات المغلقة: "
            f"{closed}\n"

            f"الرابحة: "
            f"{len(wins)}\n"

            f"الخاسرة: "
            f"{len(losses)}\n"

            f"Win Rate: "
            f"{win_rate:.1f}%\n"

            f"متوسط العائد: "
            f"{avg:+.2f}%\n"

            f"Profit Factor: "
            f"{pf:.2f}\n"

            f"الصفقات المفتوحة: "
            f"{len(self.store.state()['open_trades'])}"
        )

    # =========================================================
    # STATUS
    # =========================================================

    def status_text(self):

        state = (
            self.store.state()
        )

        return (
            "🤖 حالة النظام\n\n"

            "New Signals: "
            "MANUAL (/signal)\n"

            "Scheduler: "
            "MONITOR ONLY\n"

            f"Market: "
            f"{'OPEN' if self.market_is_open() else 'CLOSED'}\n"

            f"SAHMK Plan: "
            f"{self.s.sahmk_plan.upper()}\n"

            f"Paper Mode: "
            f"{'ON' if self.s.paper_mode else 'OFF'}\n"

            f"Paused: "
            f"{'YES' if state.get('paused') else 'NO'}\n"

            f"Universe: "
            f"{len(self.universe)}\n"

            f"Open Trades: "
            f"{len(state['open_trades'])}\n"

            f"Price Update: كل "
            f"{getattr(self.s, 'trade_price_update_minutes', 30)} دقيقة\n"

            f"Last Manual Scan: "
            f"{state['meta'].get('last_scan', '—')}\n"

            f"Last Trade Monitor: "
            f"{self.last_monitor.isoformat() if self.last_monitor else '—'}"
        )

    # =========================================================
    # HEALTH
    # =========================================================

    async def health_text(self):

        state = (
            self.store.state()
        )

        telegram_ok = False

        try:

            await self.b.signal.get_me()

            telegram_ok = True

        except Exception as exc:

            print(
                "[health] telegram failed: "
                f"{exc}"
            )

        stats = (
            self.p.stats()
            if hasattr(
                self.p,
                "stats",
            )
            else {}
        )

        return (
            "🟢 SYSTEM HEALTH\n\n"

            f"Telegram: "
            f"{'OK' if telegram_ok else 'ERROR'}\n"

            f"SAHMK local budget: "
            f"{stats.get('daily_requests', '—')}/"
            f"{stats.get('daily_limit', '—')}\n"

            f"SAHMK server remaining: "
            f"{stats.get('remaining', '—')}\n"

            f"SAHMK 429: "
            f"{stats.get('rate_limits', '—')} | "

            f"Errors: "
            f"{stats.get('errors', '—')}\n"

            f"Active Provider: "
            f"{str(stats.get('active_provider', 'sahmk')).upper()}\n"

            f"Tasilab Bulk Cooldown: "
            f"{stats.get('tasilab_bulk_cooldown_remaining', 0)}s | "
            f"Circuit: "
            f"{'OPEN' if stats.get('tasilab_circuit_open', False) else 'CLOSED'}\n"

            f"Universe Source: "
            f"{stats.get('universe_source', '—')} "
            f"({stats.get('universe_cache_size', 0)})\n"

            "Scheduler: "
            "RUNNING WHEN SERVICE IS AWAKE\n"

            f"Paper Mode: "
            f"{'ON' if self.s.paper_mode else 'OFF'}\n"

            f"Universe: "
            f"{len(self.universe)}\n"

            f"Open Trades: "
            f"{len(state['open_trades'])}\n"

            f"Last Manual Scan: "
            f"{state['meta'].get('last_scan', '—')}\n"

            f"Last Universe Update: "
            f"{state['meta'].get('last_universe_refresh', '—')}"
        )

    # =========================================================
    # SETTINGS
    # =========================================================

    def settings_text(self):

        return (
            "⚙️ الإعدادات الآمنة\n\n"

            f"SAHMK Plan: "
            f"{self.s.sahmk_plan.upper()}\n"

            f"Active-stock screen: "
            f"{self.s.manual_quotes_per_signal}\n"

            f"Detailed finalists: "
            f"{self.s.detail_quotes_per_signal}\n"

            f"Min Score: "
            f"{self.s.min_score}\n"

            f"Min Validated Probability: "
            f"{self.s.min_probability}%\n"

            f"Max Daily Signals: "
            f"{self.s.max_daily_signals}\n"

            f"Max Open Trades: "
            f"{self.s.max_open_trades}\n"

            f"Monitor Quotes/Cycle: "
            f"{self.s.trade_monitor_quotes_per_cycle}\n"

            f"Monitor Interval: "
            f"{self.s.scan_interval_seconds}s\n"

            f"Public Price Update: "
            f"{getattr(self.s, 'trade_price_update_minutes', 30)} min\n"

            f"Data Max Delay: "
            f"{self.s.data_max_delay_minutes} min\n"

            f"Min R/R: "
            f"{self.s.min_rr}\n"

            f"Target Risk/Trade: "
            f"{self.s.max_risk_per_trade:.2%} (position sizing requires account size)\n"

            f"Paper Mode: "
            f"{'ON' if self.s.paper_mode else 'OFF'}\n"

            "Secrets: HIDDEN"
        )

    # =========================================================
    # RISK
    # =========================================================

    def risk_text(self):

        return (
            "🛡️ إدارة المخاطر\n\n"

            "المخاطرة المستهدفة لكل صفقة: "
            f"{self.s.max_risk_per_trade:.2%}\n"
            "ملاحظة: حجم المركز غير محسوب لأن رأس مال المحفظة غير محدد في الإعدادات.\n"

            f"الحد الأدنى R/R: "
            f"{self.s.min_rr}\n"

            "الحد الأقصى للصفقات "
            "المفتوحة: "
            f"{self.s.max_open_trades}\n"

            f"Trailing Stop: "
            f"{'ON' if self.s.trailing_stop_enabled else 'OFF'}\n"

            "الوضع: Paper Trading فقط"
        )
