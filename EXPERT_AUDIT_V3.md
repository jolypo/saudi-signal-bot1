# Expert Audit — Saudi TASI MTF Anti-Fake v3

## Verdict

Reviewed as both a Python/Render/Telegram system and a conservative Saudi-market paper-trading engine. Material logic defects found during the review were corrected before packaging.

## Corrected in this review

1. Live signals now require 15m entry + 60m trend + daily context agreement.
2. Intraday decisions use session VWAP when datetime data is available.
3. Added same-time-slot RVOL for intraday participation when enough history exists.
4. Added price/volume divergence and buying-under-nearby-resistance hard rejects.
5. Added stricter intraday Yahoo-vs-quote price validation and historical-bar freshness.
6. Incomplete Yahoo intraday bars are dropped before analysis.
7. Added Saudi Exchange tick-size rounding to entry/stop/targets.
8. Paper exits now realize configured TP1/TP2/TP3 allocations instead of pretending 100% exits at TP3/SL.
9. Net paper P&L includes estimated round-trip fee/slippage costs.
10. TP1 break-even protection works independently of ATR trailing.
11. SAHMK 403/5xx can fall back to Tasilab for that call while preserving the daily/temporary 429 policy.
12. render.yaml was aligned with v3 defaults (score 82, R/R 1.8, signal window, bulk size 20, fee baseline 15.5 bps).
13. Signal target explanation now matches the actual R-multiple logic.

## Live-signal hard rejects

A signal is blocked despite a high score when key evidence is unsafe, including missing TASI context, bearish TASI, weak participation, weak ADX/DI control, 60m disagreement, falling daily context, overextension from VWAP/EMA20, failed breakout, large upper wick/volume climax, price-volume divergence, or buying directly below resistance.

## Remaining intentional limitations

- Sector relative strength is not fabricated because the current feeds do not provide a sufficiently reliable live sector series in this project.
- Probability is empirical only after at least the configured minimum closed-paper samples; before then it remains UNVALIDATED.
- Data is delayed, so this is short-swing signal logic rather than true low-latency day trading.
- MAX_RISK_PER_TRADE is a target policy only until a paper-account size is configured; the project does not invent a position size.
- JSON state on Render Free should be treated as ephemeral across replacement/redeploy events.
- No Level-2/order-book imbalance model is claimed because the current data feed does not provide dependable depth data for it.

## Validation

- 33 automated tests passed.
- Python compileall passed.
- render.yaml parsed successfully.
- Secret scan found no embedded Telegram tokens or API keys.
- Build package excludes .env, caches, bytecode and runtime state files.
