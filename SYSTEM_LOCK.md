# ApexHunter 2.0 — Production Lock Specification

## Status: LOCKED & VERIFIED FOR PRODUCTION (ApexHunter 2.0)
**Release Date:** November 24, 2026  
**Architecture:** Unified Dual-Engine MetaTrader 5 Trading System  
**Verification:** 100% PASS (95/95 Unit and Integration Tests Passing, Zero Prop Firm Breaches across 10.5 Months)

---

### 1. Immutable Core Production Files

The following files represent the verified production execution environment and must NOT be modified without formal approval and full regression test execution:

| File | Subsystem | Protection Level |
| :--- | :--- | :---: |
| `run_apexhunter_2.py` | Production CLI Root Launcher | **LOCKED** |
| `lk_system/master_live_bot.py` | Production Unified Live Orchestrator & Surveillance Daemon | **LOCKED** |
| `lk_system/core/apexhunter_engine.py` | ApexHunter v1.2.1 TripleGuard Engine (Gold/Nasdaq) | **LOCKED** |
| `lk_system/core/asset_handler.py` | Dynamic 4-Stage Broker Symbol Auto-Resolver & Market Watch Manager | **LOCKED** |
| `lk_system/core/persistence_manager.py` | Atomic State Serialization & Startup Live MT5 Reconciler | **LOCKED** |
| `lk_system/core/mt5_live_bridge.py` | Microsecond C++ IPC Data Bridge with Auto-Reconnect Watchdog | **LOCKED** |
| `lk_system/core/risk_guardian.py` | 00:00 Server Time Prop Firm Drawdown & Circuit Breaker Engine | **LOCKED** |
| `lk_system/core/spread_guardian.py` | Live Floating Spread Caps & 30% SL Filter | **LOCKED** |
| `lk_system/core/news_guardian.py` | Automated Economic News Calendar Embargo Shield | **LOCKED** |
| `lk_system/live/notifier.py` | Decoupled Asynchronous Telegram & Discord Telemetry Engine | **LOCKED** |
| `lk_system/live/order_manager.py` | Multi-Split MT5 Order Execution & Breakeven Monitor | **LOCKED** |
| `lk_system/backtest/unified_backtest_engine.py` | 1-to-1 Live Parity Portfolio Backtest Engine | **LOCKED** |

---

### 2. Locked Strategy Sizing & Shield Parameters

- **Commodities & Indices (ApexHunter v1.2.1):**
  - Gold (`XAUUSD`): SL Multiplier = $0.90 \times \text{ATR}$, TP1 = $1.40\text{R}$ (50%), TP2 = $2.20\text{R}$ (50%), Max Spread = $0.65\text{ pts}$.
  - Nasdaq (`NAS100`): SL Multiplier = $1.40 \times \text{ATR}$, TP1 = $1.40\text{R}$ (50%), TP2 = $2.10\text{R}$ (50%), Max Spread = $7.00\text{ pts}$.
  - TripleGuard Volatility Shields: Stretch $\ge 0.40$, ADX $\le 45.0$, Chase $\le 0.50$, Low-ADX Stretch $\ge 0.90$.
  - Time Filters: 09:00 & 13:00 UTC allowed only if $\text{Stretch} \ge 1.10$. Monday 14:00–17:00 UTC blocked.
- **Forex Basket (SMC Engine):**
  - Pairs: `EURUSD`, `GBPUSD`, `AUDUSD`, `USDJPY`, `EURJPY`, `USDCAD`.
  - Splits: 30% TP1 @ $2.50\text{R}$, 30% TP2 @ $5.00\text{R}$, 40% TP3 @ $8.00\text{R}$.
  - Asian Range: 00:00–06:00 UTC extreme sweeps required during London/NY.
- **Prop Firm Risk Boundaries:**
  - Daily Drawdown: 5.0% from 00:00 server-time $\max(\text{balance}, \text{equity})$ anchor.
  - Daily Circuit Breaker: 4.0% internal trip floor.
  - Max Drawdown Floor: 10.0% from initial base capital ($4,500 on $5k, $9,000 on $10k).
  - Max Daily Losses: 2 losses per symbol per day.
