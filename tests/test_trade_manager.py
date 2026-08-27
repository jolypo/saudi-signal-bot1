from types import SimpleNamespace

from app.database.json_store import JsonStore
from app.trades.manager import TradeManager


class Sig:
    symbol = "1120"
    entry = 100.0
    def to_dict(self):
        return {
            "symbol": "1120", "name": "Test", "entry": 100.0,
            "sl": 98.0, "tp1": 102.0, "tp2": 103.0, "tp3": 104.0,
        }


def settings():
    return SimpleNamespace(
        max_open_trades=5, fee_bps=15.5, slippage_bps=5,
        tp1_percent=30, tp2_percent=30, tp3_percent=40,
        trailing_after_tp1_to_entry=True, trailing_stop_enabled=False,
        trailing_after_tp2_atr=1.0,
    )


def test_partial_exits_are_weighted_and_tp1_moves_to_break_even(tmp_path):
    store = JsonStore(str(tmp_path))
    tm = TradeManager(store, settings())
    assert tm.add(Sig())
    trade, events = tm.update("1120", 102.0)
    assert "TP1" in events
    assert trade["remaining_position_pct"] == 70.0
    assert trade["realized_result_pct"] > 0
    assert tm.apply_trailing(trade, 102.0)
    assert trade["trailing_stop"] == 100.0


def test_stop_after_tp1_keeps_realized_partial_profit(tmp_path):
    store = JsonStore(str(tmp_path))
    tm = TradeManager(store, settings())
    tm.add(Sig())
    trade, _ = tm.update("1120", 102.0)
    tm.apply_trailing(trade, 102.0)
    # At BE the remaining 70% still pays estimated costs, while TP1 banked 30%.
    closed, events = tm.update("1120", 100.0)
    assert "SL" in events
    assert closed["status"] == "CLOSED_SL"
    assert closed["remaining_position_pct"] == 0.0
    assert closed["result_pct"] is not None
