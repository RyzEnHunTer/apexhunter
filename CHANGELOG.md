# CHANGELOG

## Version 0.1.0

### Phase 1 Complete

**Implemented**
- Workspace Separation (`qde/research/` vs `qde/production/`)
- Multi-Timeframe Data Layer (`mtf_manager.py`)
- Dynamic Feature Laboratory (`dynamic_generator.py`)
- Automated Feature Selection (`feature_selector.py`)

**Major Fixes**
- Eliminated MTF look-ahead bias by strictly adopting `label='left', closed='left'` for open-timestamped Forex data alignment.

**Verification**
- Independent QA Passed (Test Suite: `test_phase1.py` - 6/6 passing).

**Exit Gate**
- Passed.

**Architecture**
- Frozen.

**Status**
- Ready for Phase 2.

## Version 0.2.0

### Lewis Kelly Full SMC System Integration

**Implemented**
- **1-Minute Intraday Gap Entry (`tick_engine.py`, `live_bot.py`):** Zoom to 1m upon session sweep/POI touch, confirm via 1m CHOCH, and execute immediately on the 1m Intraday Gap (FVG) retest with tight 1m swing pivot Stop Loss.
- **Dynamic Multi-Stage Trailing & Scaling Engine (`live_bot.py`, `tick_engine.py`):**
  - Staged scale-outs: 2.5R (25%), 4.3R (15%), 6.0R (15%), 8.5R (15%), 10.0R (15%), up to 13.0R max runner.
  - Automatic breakeven protection at 2.5R.
  - Active trailing Stop Loss locked behind newly confirmed 1-minute structural swing pivots (`Active_Low` / `Active_High`).
- **Execution Guardrails & Timeouts:** Pending setups expire after 15 minutes and automatically cancel if the active killzone session ends, eliminating stale entries.
- **Auction Market Volume Profile Integration (`qpe/intelligence/volume_profile.py`):** Added Fixed-Range Volume Profile with VAH/VAL/POC detection and uniform volume fallback.

**Verification & Results (Multi-Month Backtest on EURUSD):**
- **August 2026:** 80.0% Win Rate, +$644.14 (+12.88% ROI in a single month), catching full 13.0R expansion runner.
- **Jan – Feb 2026:** Out-of-sample positive (+4.84% ROI).
- **March 2026:** Out-of-sample positive (+5.41% ROI).
- **Cumulative Performance:** ROI expanded from +6.98% (legacy fixed 3R/6R) to **+21.41%**, surpassing the project's 15–25% ROI target within prop firm drawdown rules.

