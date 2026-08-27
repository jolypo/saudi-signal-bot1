from datetime import datetime, timezone


class TradeManager:
    def __init__(self, store, settings):
        self.store = store
        self.s = settings

    def _round_trip_cost_pct(self):
        return (
            2.0 * float(getattr(self.s, "fee_bps", 0.0))
            + 2.0 * float(getattr(self.s, "slippage_bps", 0.0))
        ) / 100.0

    def _leg_net_pct(self, entry, exit_price):
        gross = (float(exit_price) - float(entry)) / float(entry) * 100.0
        return gross - self._round_trip_cost_pct(), gross

    def add(self, signal):
        state = self.store.state()
        trades = state["open_trades"]
        if len(trades) >= self.s.max_open_trades:
            return False

        trade = dict(signal) if isinstance(signal, dict) else signal.to_dict()
        symbol = str(trade.get("symbol", "")).strip()
        if not symbol:
            return False
        if any(str(x.get("symbol", "")) == symbol for x in trades):
            return False

        trade.update(
            {
                "status": "OPEN",
                "current_price": float(trade["entry"]),
                "max_profit_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "tp1_hit": False,
                "tp2_hit": False,
                "tp3_hit": False,
                "sl_hit": False,
                "profit_alerts_sent": [],
                "near_sl_warning_sent": False,
                "trailing_stop": None,
                "exit": None,
                "exit_time": None,
                "result": None,
                "result_pct": None,
                "gross_result_pct": None,
                "realized_result_pct": 0.0,
                "remaining_position_pct": 100.0,
                "partial_exits": [],
                "estimated_round_trip_cost_pct": self._round_trip_cost_pct(),
            }
        )
        trades.append(trade)
        self.store.save_state(state)
        return True


    def set_signal_message_ids(self, symbol, message_ids):
        """Persist the original Telegram signal message id per destination chat."""
        state = self.store.state()
        changed = False
        for trade in state.get("open_trades", []):
            if str(trade.get("symbol")) == str(symbol):
                trade["signal_message_ids"] = {str(k): int(v) for k, v in (message_ids or {}).items()}
                changed = True
                break
        if changed:
            self.store.save_state(state)
        return changed

    def remove_open(self, symbol):
        state = self.store.state()
        before = len(state.get("open_trades", []))
        state["open_trades"] = [
            x for x in state.get("open_trades", [])
            if str(x.get("symbol", "")) != str(symbol)
        ]
        changed = len(state["open_trades"]) != before
        if changed:
            self.store.save_state(state)
        return changed

    def _realize_leg(self, trade, key, price, allocation_pct):
        allocation_pct = max(0.0, min(float(allocation_pct), float(trade.get("remaining_position_pct", 100.0))))
        if allocation_pct <= 0:
            return
        net_leg, gross_leg = self._leg_net_pct(trade["entry"], price)
        weighted = net_leg * allocation_pct / 100.0
        trade["realized_result_pct"] = float(trade.get("realized_result_pct", 0.0)) + weighted
        trade["remaining_position_pct"] = max(0.0, float(trade.get("remaining_position_pct", 100.0)) - allocation_pct)
        trade.setdefault("partial_exits", []).append({
            "level": key,
            "price": float(price),
            "allocation_pct": allocation_pct,
            "gross_leg_pct": gross_leg,
            "net_leg_pct": net_leg,
            "weighted_result_pct": weighted,
            "time": datetime.now(timezone.utc).isoformat(),
        })

    def update(self, symbol, price):
        state = self.store.state()
        for trade in state["open_trades"]:
            if trade["symbol"] != symbol:
                continue

            entry = float(trade["entry"])
            price = float(price)
            current_net_pct, current_gross_pct = self._leg_net_pct(entry, price)
            trade["current_price"] = price
            trade["gross_result_pct"] = current_gross_pct
            trade["estimated_round_trip_cost_pct"] = self._round_trip_cost_pct()
            trade["max_profit_pct"] = max(float(trade.get("max_profit_pct", 0)), current_net_pct)
            trade["max_drawdown_pct"] = min(float(trade.get("max_drawdown_pct", 0)), current_net_pct)
            events = []

            effective_sl = float(trade.get("trailing_stop") or trade["sl"])
            if price <= effective_sl:
                remaining = float(trade.get("remaining_position_pct", 100.0))
                if remaining > 0:
                    self._realize_leg(trade, "SL", price, remaining)
                trade["sl_hit"] = True
                trade["status"] = "CLOSED_SL"
                trade["exit"] = price
                trade["exit_time"] = datetime.now(timezone.utc).isoformat()
                trade["result_pct"] = float(trade.get("realized_result_pct", 0.0))
                trade["result"] = "WIN" if trade["result_pct"] > 0 else "LOSS"
                events.append("SL")
            else:
                allocations = {
                    "tp1": float(getattr(self.s, "tp1_percent", 30.0)),
                    "tp2": float(getattr(self.s, "tp2_percent", 30.0)),
                    "tp3": float(getattr(self.s, "tp3_percent", 40.0)),
                }
                for key in ("tp1", "tp2", "tp3"):
                    hit_key = f"{key}_hit"
                    if price >= float(trade[key]) and not trade.get(hit_key, False):
                        trade[hit_key] = True
                        allocation = allocations[key]
                        # TP3 closes any remainder so percentages cannot strand a position.
                        if key == "tp3":
                            allocation = float(trade.get("remaining_position_pct", allocation))
                        self._realize_leg(trade, key.upper(), price, allocation)
                        events.append(key.upper())

                if trade.get("tp3_hit") and trade.get("status") == "OPEN":
                    trade["status"] = "CLOSED_TP3"
                    trade["exit"] = price
                    trade["exit_time"] = datetime.now(timezone.utc).isoformat()
                    trade["result_pct"] = float(trade.get("realized_result_pct", 0.0))
                    trade["result"] = "WIN" if trade["result_pct"] > 0 else "LOSS"
                    events.append("CLOSE_TP3")

            if trade.get("status", "").startswith("CLOSED"):
                history = self.store.history()
                history.append(dict(trade))
                state["open_trades"] = [x for x in state["open_trades"] if x is not trade]
                self.store.save_history(history)

            self.store.save_state(state)
            return trade, events

        return None, []

    def apply_trailing(self, trade, price, atr=None):
        if not trade or trade.get("status") != "OPEN":
            return False
        changed = False
        current = float(trade.get("trailing_stop") or trade["sl"])
        new_stop = current

        # Move to break-even after TP1 even when ATR trailing is disabled.
        if trade.get("tp1_hit") and getattr(self.s, "trailing_after_tp1_to_entry", True):
            new_stop = max(new_stop, float(trade["entry"]))

        # ATR trailing after TP2 is separately controlled by TRAILING_STOP_ENABLED.
        if (
            getattr(self.s, "trailing_stop_enabled", False)
            and trade.get("tp2_hit")
            and atr
            and atr > 0
        ):
            new_stop = max(new_stop, float(price) - float(atr) * self.s.trailing_after_tp2_atr)

        if new_stop > current:
            trade["trailing_stop"] = round(new_stop, 2)
            changed = True
            # Persist immediately so a subsequent monitor tick cannot reload the
            # pre-trailing state before TradingService performs its later save.
            state = self.store.state()
            for item in state.get("open_trades", []):
                if item.get("symbol") == trade.get("symbol"):
                    item["trailing_stop"] = trade["trailing_stop"]
                    break
            self.store.save_state(state)
        return changed
