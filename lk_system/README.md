# ApexHunter Institutional Unified Trading System

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Platform MT5](https://img.shields.io/badge/platform-MetaTrader%205-orange.svg)](https://www.metatrader5.com/)
[![Tests Passing](https://img.shields.io/badge/tests-47%2F47%20passing-brightgreen.svg)]()

Institutional-grade, multi-asset algorithmic execution system combining **DCC ApexHunter v1.2.1** and **Lewis Kelly SMC** running **simultaneously in parallel (no switcher)** across 8 live markets on MetaTrader 5.

---

## Quick Start Guide

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Settings
```bash
copy live\config.example.json live\config.json
copy live\bot_accounts_config.example.json live\bot_accounts_config.json
```

### 3. Run Strategy Diagnostics & Tests
```bash
# Run all 47 unit & parity tests
python -m unittest discover tests

# Run strategy calculation diagnostics
python master_live_bot.py --test-strategy
```

### 4. Launch Bot
```bash
# Interactive Control Center
python master_live_bot.py
# Or 1-Click Launcher
start_master_bot.bat
```

For full details, see the parent [README.md](../README.md).
