from app.risk.levels import build_long_levels
def test_levels():
 x=build_long_levels(100,101,2,97,1.5);assert x["sl"]<x["entry"]<x["tp1"]<x["tp3"]

from app.risk.levels import saudi_tick_size, round_saudi_price

def test_saudi_tick_bands_and_level_rounding():
    assert saudi_tick_size(24.99) == 0.01
    assert saudi_tick_size(25.00) == 0.02
    assert saudi_tick_size(50.00) == 0.05
    assert saudi_tick_size(100.00) == 0.10
    assert saudi_tick_size(250.00) == 0.20
    assert saudi_tick_size(500.00) == 0.50
    assert round_saudi_price(25.031, "floor") == 25.02
    assert round_saudi_price(25.031, "ceil") == 25.04

def test_short_swing_stop_is_wider_than_day_trade_stop():
    day = build_long_levels(99.9, 100.1, atr=1.0, support=None, rr_min=1.8, trade_type="مضاربة يومية")
    swing = build_long_levels(99.9, 100.1, atr=1.0, support=None, rr_min=1.8, trade_type="سوينغ قصير 2–5 جلسات")
    assert day is not None and swing is not None
    assert swing["sl"] < day["sl"]
    assert swing["rr_tp1"] >= 1.8
