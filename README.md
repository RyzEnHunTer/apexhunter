# ApexHunter Institutional Unified Trading System

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Platform MT5](https://img.shields.io/badge/platform-MetaTrader%205-orange.svg)](https://www.metatrader5.com/)
[![Tests Passing](https://img.shields.io/badge/tests-47%2F47%20passing-brightgreen.svg)]()
[![License MIT](https://img.shields.io/badge/license-MIT-green.svg)]()

Institutional-grade, multi-asset algorithmic execution system combining **DCC ApexHunter v1.2.1** and **Lewis Kelly SMC** running **simultaneously in parallel (no switcher)** across 8 live markets on MetaTrader 5.

---

## Key Features

- **Simultaneous Dual-Engine Execution:**
  - **DCC ApexHunter v1.2.1:** Trades Gold (`XAUUSD`) & Nasdaq (`NAS100`) on M5 closed bars with 1H trend bias confirmation, Smart Killzones (09:00 & 13:00 UTC), and TripleGuard Volatility Shields.
  - **Lewis Kelly SMC Forex Basket:** Trades 6 major pairs (`EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `EURJPY`, `USDCAD`) on M15 closed bars via 00:00–06:00 UTC Asian session extreme liquidity sweeps during London and NY sessions.
- **Dual-Circuit Breaker (Prop Firm Protection):**
  - **4.0% Daily Circuit Breaker:** Anchored daily at 00:00 UTC. Pre-CB trades are clamped so daily loss never overshoots the 4.0% limit.
  - **8.5% Max Floor Circuit Breaker:** Halts trading cleanly before reaching the 10% hard liquidation floor, entering a 0.20% recovery buffer.
- **High-Impact News Guardian:**
  - Automated 10-minute pre-news auto-close for all affected open positions.
  - 20-minute pre-event and 15-minute post-event trade embargo.
  - Automatic 12-hour background synchronization with Forex Factory.
- **Spread Guardian:**
  - Golden Number absolute spread caps per asset.
  - Maximum 30% of Stop Loss distance spread filter.
- **Server-Side Split Orders & Breakeven Protection:**
  - ApexHunter: 2-way split (50% TP1 @ 1.4R, 50% TP2 @ 2.1R). Moves SL to BE upon TP1 hit.
  - Lewis Kelly SMC: 3-way split (30% TP1 @ 2.5R, 30% TP2 @ 5.0R, 40% TP3 @ 8.0R). Moves SL to BE upon TP1 hit.
- **Multi-Account Lifecycle & Prop Firm Evaluator:**
  - Tracks Evaluation Phase 1, Phase 2, and Funded Mode.
  - Automated Target Lock once milestone is banked.
- **Forensic Audit Logging System:**
  - `logs/market_calculations_YYYYMMDD.csv`: 37-column granular calculation audit of every bar.
  - `logs/market_surveillance_YYYYMMDD.log`: Chronological market timeline.
  - `logs/live_engine_YYYYMMDD.log`: Execution events, broker retcodes, and error traces.
  - `logs/live_state.json`: Real-time atomic 5-second snapshot.

---

## Directory Architecture

```text
apexhunter/
├── README.md
├── lk_system/
│   ├── master_live_bot.py            # Primary Production Unified Bot
│   ├── master_regime_bot.py          # Redirect wrapper
│   ├── start_master_bot.bat          # 1-Click Interactive Terminal Console
│   ├── dcc_engine_v1_2.py            # DCC Engine export shim
│   ├── requirements.txt              # Pinned production dependencies
│   ├── core/                         # Production Engines & Safeguards
│   │   ├── account_config_manager.py # Persistent Account Lifecycle & Target Manager
│   │   ├── apexhunter_engine.py      # DCC ApexHunter v1.2.1 Engine
│   │   ├── audit_logger.py           # Multi-tier Forensic Logging Engine
│   │   ├── mt5_live_bridge.py        # MT5 Connection & Multi-TF Fetching
│   │   ├── news_guardian.py          # News Protection & Embargoes
│   │   ├── order_manager.py          # Split Orders & Dynamic Breakeven Shifts
│   │   ├── risk_guardian.py          # Circuit Breakers & Dynamic Lot Sizing
│   │   ├── spread_guardian.py        # Golden Caps & Spread-to-SL Filters
│   │   └── ui_renderer.py            # Rich Terminal UI & Telemetry
│   ├── live/                         # Live Configuration Templates
│   │   ├── config.example.json       # Central Configuration Template
│   │   ├── bot_accounts_config.example.json # Account Lifecycle Template
│   │   ├── live_bot.py               # Live Bot Entrypoint
│   │   └── notifier.py               # Telegram & Discord Alert Dispatcher
│   ├── tests/                        # 47 Unit, Integration & Parity Tests
│   │   ├── test_live_vs_backtest_parity.py # Backtest Parity Proof
│   │   ├── test_strategy_logic_diagnostics.py # Diagnostic Suite
│   │   └── test_master_bot_smoke.py  # 3-Cycle Smoke Test
│   ├── research/                     # Backtest engines & parameter sweeps
│   └── logs/                         # Production audit logs (ignored in git)
```

---

## Quick Start (VPS / Local)

### 1. Prerequisites
- **OS:** Windows 10/11 or Windows Server (VPS)
- **Python:** Python 3.11.x 64-bit (Recommended) or Python 3.12.x 64-bit
- **MetaTrader 5:** Installed and logged into your broker account with **"Algo Trading" enabled**.

### 2. Clone the Repository
```bash
git clone https://github.com/RyzEnHunTer/apexhunter.git
cd apexhunter/lk_system
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Account & Settings
Copy the example templates:
```bash
copy live\config.example.json live\config.json
copy live\bot_accounts_config.example.json live\bot_accounts_config.json
```

### 5. Verify Installation
Run the complete test suite and strategy diagnostics:
```bash
# Run all 47 unit & parity tests
python -m unittest discover tests

# Run strategy calculation diagnostics
python master_live_bot.py --test-strategy
```

### 6. Launch the Bot
Double-click `start_master_bot.bat` or run:
```bash
python master_live_bot.py
```
From the interactive menu:
- Select **`[1]`** for **LIVE Order Execution** on MT5.
- Select **`[2]`** for **DRY-RUN Paper Trading** (zero capital risk forward testing).
- Select **`[3]`** to manage **Lifecycle Phases & Targets**.
- Select **`[4]`** to configure **Telegram / Discord Alerts**.

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
