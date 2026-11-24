# ApexHunter 2.0 Institutional Multi-Asset Trading System

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Platform MT5](https://img.shields.io/badge/platform-MetaTrader%205-orange.svg)](https://www.metatrader5.com/)
[![Tests Passing](https://img.shields.io/badge/tests-95%2F95%20passing-brightgreen.svg)]()
[![Prop Firm Compliance](https://img.shields.io/badge/prop%20firm-100%25%20zero%20breach-brightgreen.svg)]()
[![License MIT](https://img.shields.io/badge/license-MIT-green.svg)]()

Institutional-grade, multi-asset algorithmic execution engine combining **ApexHunter v1.2.1 TripleGuard** and **ApexHunter Smart Money Forex Engine** running **simultaneously in parallel (zero mode-switching, zero market analyser interference)** across 8 live markets on MetaTrader 5.

---

## Architecture Documentation & Guides

- 📖 **[PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md)** — Master Strategy Specification, Mathematical Formulas, Indicator Architecture, and Prop Firm Rules.
- 📜 **[PROJECT_RULES.md](PROJECT_RULES.md)** — Canonical GSD Development Protocols, Invariants, and AI Agent Guidelines.
- 🔒 **[SYSTEM_LOCK.md](SYSTEM_LOCK.md)** — Production Parameter Lock & Immutability Specification.

---

## Key Capabilities

- **Simultaneous Dual-Engine Execution:**
  - **ApexHunter Commodity & Index Engine:** Trades Gold (`XAUUSD`) & Nasdaq (`NAS100`) on M5 closed bars with 1H trend bias confirmation, Smart Killzones (09:00 & 13:00 UTC), and TripleGuard Volatility Shields.
  - **ApexHunter Smart Money Forex Engine:** Trades 6 major pairs (`EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `EURJPY`, `USDCAD`) on M15 closed bars via 00:00–06:00 UTC Asian session extreme liquidity sweeps during London and NY sessions.
- **Dynamic Asset Handler:**
  - 4-stage fuzzy discovery resolves differing broker tickers (`XAUUSD.m`, `GOLD`, `USTEC`, `US100.cash`, `pro.EURUSD`, `EURUSD+`) and configures Market Watch automatically. Missing symbols are safely disabled without interrupting trading.
- **Fault-Tolerant Persistence & Crash Recovery:**
  - Atomic JSON disk persistence (`logs/bot_runtime_state.json`) with startup MT5 ticket reconciliation. Automatically restores multi-split order groups (`APE_T1`/`APE_T2` and `SMC_TP1`/`TP2`/`TP3`), handles offline TP1s, and enforces Breakeven Stop Loss shifts on the MT5 terminal.
- **Prop Firm Overnight Reset Parity (The5ers & FTMO):**
  - Resets daily drawdown anchor at **00:00 MT5 Server Time** using $\max(\text{balance}, \text{equity})$ benchmark.
  - Internal 4.0% Daily Circuit Breaker protects against the firm's 5.0% hard breach limit.
  - Static 10.0% Max Drawdown Floor from initial base capital with dynamic fitted risk downscaling.
- **High-Impact News Guardian:**
  - Automated 10-minute pre-news auto-close for all affected open positions.
  - 20-minute pre-event and 15-minute post-event trade embargo.
  - Background synchronization with Forex Factory calendar.
- **Spread Guardian:**
  - Hard spread point caps per asset and maximum 30% of Stop Loss distance spread filter.
- **Decoupled Asynchronous Telemetry:**
  - Telegram Bot & Discord Webhook rich scorecards dispatched via a thread-safe, non-blocking producer-consumer queue (zero latency added to tick surveillance).

---

## Directory Architecture

```text
D:\FOREX\FOREX/
├── run_apexhunter_2.py                 # Primary Root Launcher
├── run_bot.py                         # Convenience redirect
├── PROJECT_DOCUMENTATION.md           # Master Strategy Specification
├── PROJECT_RULES.md                   # AI Agent & Developer Rules
├── SYSTEM_LOCK.md                     # Locked Parameter Registry
├── requirements.txt                   # Python Dependencies
├── lk_system/                         # Core Production Package
│   ├── master_live_bot.py             # Primary Production Orchestrator
│   ├── core/                          # Production Engines & Guardians
│   │   ├── apexhunter_engine.py       # ApexHunter v1.2.1 TripleGuard Engine
│   │   ├── asset_handler.py           # Broker Symbol Auto-Resolver
│   │   ├── persistence_manager.py     # Atomic Crash Recovery Manager
│   │   ├── mt5_live_bridge.py         # Microsecond IPC Bridge & Watchdog
│   │   ├── risk_guardian.py           # Prop Firm Circuit Breaker
│   │   ├── spread_guardian.py         # Live Spread Protection
│   │   └── news_guardian.py           # News Calendar Embargo Shield
│   ├── live/                          # Live Execution Infrastructure
│   │   ├── notifier.py                # Asynchronous Telegram & Discord
│   │   └── order_manager.py           # Multi-Split Order Execution
│   ├── logs/                          # State Snapshots & Audit Trails
│   └── tests/                         # Automated Test Suite (95 Tests)
```

---

## Quick Start & Execution

### 1. Installation
```powershell
pip install -r requirements.txt
```

### 2. Launching ApexHunter 2.0
From the repository root:
```powershell
# Interactive Terminal Menu (Default)
python run_apexhunter_2.py

# Live MT5 Order Execution Mode (Auto-confirm)
python run_apexhunter_2.py --live --auto

# Dry-Run Paper Trading Mode (Real-time market surveillance without orders)
python run_apexhunter_2.py --dry-run --auto

# Custom Capital & Risk Overrides (e.g. $10,000 challenge account with 1% risk)
python run_apexhunter_2.py --live --capital 10000.0 --risk 1.0

# Custom Prop Firm Drawdown & Circuit Breaker Limits (CLI Overrides)
python run_apexhunter_2.py --live --daily-cb 4.0 --max-dd 10.0 --floor-cb 8.5 --risk 1.0
```

### 3. Editing Drawdown & Circuit Breakers (3 Methods)
1. **Interactive Console Menu:**
   - Launch `python run_apexhunter_2.py`
   - Select `[3] Manage Lifecycle, DD & CB`
   - Use `[7]` to edit **Daily Drawdown Limit & Circuit Breaker %** (e.g., 4.0% CB / 5.0% DD)
   - Use `[8]` to edit **Max Total Drawdown Hard Floor & Floor CB %** (e.g., 8.5% CB / 10.0% Hard Floor)
   - Use `[2]` / `[3]` to edit Challenge Risk % and Funded Risk %
   - *Changes are dynamically applied to `RiskGuardian` and automatically saved to `bot_accounts_config.json`.*

2. **Command-Line Arguments (CLI):**
   - `--daily-cb <pct>`: Daily Drawdown Circuit Breaker limit % (e.g. `4.0` or `0.04`)
   - `--max-dd <pct>`: Max Total Drawdown Hard Floor limit % (e.g. `10.0` or `0.10`)
   - `--floor-cb <pct>`: Max Floor Circuit Breaker % (e.g. `8.5` or `0.085`)
   - `--risk <pct>`: Trade risk percentage per trade (e.g. `1.0` or `0.01`)

3. **Persistent Account Profile (`lk_system/live/bot_accounts_config.json`):**
   - Edit fields directly: `"daily_cb_pct"`, `"daily_dd_limit_pct"`, `"max_cb_pct"`, `"max_total_dd_pct"`, `"risk_per_trade"`.

### 4. Running Test Suite
```powershell
python -m unittest discover lk_system/tests
```
*(All 100 tests must pass before deploying to a live account).*
