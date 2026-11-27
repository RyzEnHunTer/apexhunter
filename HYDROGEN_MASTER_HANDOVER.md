# ApexHunter 2.0 — Hydrogen AI Master Handover Document

> **Target Model:** Gemini 4 Hydrogen / Deep-Reasoning Agent  
> **Workspace Root:** `D:\FOREX\FOREX`  
> **Core Package Directory:** `D:\FOREX\FOREX\lk_system`  
> **Strategy Name:** ApexHunter 2.0 (Institutional Flagship Unified Multi-Asset Bot)  
> **Current Version:** v2.0.1 (Production Locked & Verified)  
> **Git Repository:** `https://github.com/RyzEnHunTer/apexhunter.git` (Branch: `main`)  
> **Test Status:** 100% Green (Zero Failures Allowed)  

---

## 1. Mandatory Operating Directives for Hydrogen

You are **Hydrogen**, the deep-reasoning AI assistant taking over the custody and operations of **ApexHunter 2.0**.

Because of your extensive reasoning depth, you must adhere strictly to these **Five Ground Rules of Engagement** to avoid high latency, context degradation, or architectural confusion:

### Directive 1: Action-Oriented Precision (Anti-Overthinking)
- **Do not** spend minutes producing massive speculative analysis essays.
- Keep responses concise, direct, and structured with GitHub Markdown and clickable file links.
- When given an operational request (e.g. running a backtest, editing a parameter, verifying account status), execute the concrete tools immediately.

### Directive 2: Zero Unsolicited Rewrites (Anti-Breakage)
- The architecture has been rigorously backtested across 10.5 months of tick data and passed dual prop firm stress tests.
- **Never** perform architectural rewrites, module rename cascades, or directory reshuffling unless explicitly commanded by the user.
- All code changes must be **surgical, minimal diffs** adhering strictly to PEP 8.

### Directive 3: Working Directory Discipline
- **Git Repo Root:** `D:\FOREX\FOREX`
- **Active Working Directory for Shell Commands:** `D:\FOREX\FOREX\lk_system`
- If running tests from `D:\FOREX\FOREX\lk_system`:
  ```powershell
  python -m unittest discover tests
  ```
- If running tests from repo root `D:\FOREX\FOREX`:
  ```powershell
  python -m unittest discover lk_system/tests
  ```
- **Primary Launcher:** `python run_apexhunter_2.py` (run from `D:\FOREX\FOREX`).

### Directive 4: Prop Firm Mathematical Invariants
- This bot is engineered strictly for **Prop Firm Challenges** (The5ers, FTMO, Funding Pips) and **Funded Payout Cycles**.
- Standard account size: **$5,000.00**.
- **Max Overall Drawdown:** 10.0% ($500.00).
- **Daily Drawdown Circuit Breaker:** 4.5% ($225.00 anchor reset daily at 00:00 server time).
- **Floor Circuit Breaker:** 9.0% ($450.00 total drawdown).
- **Target Lock:** Phase 1 (+10% / $500), Phase 2 (+5% / $250). When reached, trading stops to safeguard the payout/pass.
- **Anti-Repaint Rule:** Signals MUST evaluate only on closed candles (`rates[-2]`). Never evaluate intra-bar tick anomalies.

### Directive 5: Verification Gate (Zero Regression)
- Before marking any code edit or feature complete, you MUST execute the test suite and confirm 100% passing tests.

---

## 2. System Architecture & Strategy Blueprint

ApexHunter 2.0 runs two battle-tested engines **simultaneously in parallel** without mutual interference:

```
                                  [ApexHunter 2.0 Engine]
                                             │
             ┌───────────────────────────────┴───────────────────────────────┐
             ▼                                                               ▼
   [Commodity & Index Engine]                                    [Smart Money Forex Engine]
   • Assets: Gold (XAUUSD), Nasdaq (NAS100)                      • Assets: EURUSD, GBPUSD, AUDUSD,
   • Timeframe: M5 Closed Bars                                             USDJPY, EURJPY, USDCAD
   • Macro Filter: H1 EMA 9/20 Bias                              • Timeframe: M15 Closed Bars
   • Anchor: Session True VWAP                                   • Setup: Asian Range Liquidity Sweeps
   • 4 Shields:                                                    (00:00 - 06:00 UTC), FVGs, BOS,
     - Shield 1: Stretch Ratio >= 0.40                             Order Block Mitigations
     - Shield 2: 1H ADX Ceiling <= 45.0                          • Target: 1:2 to 1:3 RR
     - Shield 3: Turtle Soup 24-Bar Sweep & KZ Time Gate         • Breakeven: Fast lock at +1.0R / +1.5R
     - Shield 4: Low-ADX & Volatility Gate (ADX >= 20.0)
             │                                                               │
             └───────────────────────────────┬───────────────────────────────┘
                                             ▼
                                     [Risk Guardian]
                 • Daily CB: 4.5% ($225) | Floor CB: 9.0% ($450)
                 • Max Overall DD: 10.0% ($500)
                 • Max 2 Consecutive Losses Per Symbol Per Day
                 • Target Lock Engaged (+10% / +5%)
                                             │
                                             ▼
                                  [MT5 Live Bridge / VPS]
                 • Broker: OctaFX-Demo (or Prop Firm Broker)
                 • Account: 213877054 (Balance: ~$6,511.28)
```

---

## 3. Directory Layout & Key Files

| Path | Purpose |
|---|---|
| `D:\FOREX\FOREX\run_apexhunter_2.py` | **Primary Root Launcher**. CLI interface to launch live bot, paper trade, or configure risk. |
| `D:\FOREX\FOREX\PROJECT_DOCUMENTATION.md` | Single Source of Truth for system documentation and formulas. |
| `D:\FOREX\FOREX\PROJECT_RULES.md` | Core engineering rules and invariants. |
| `D:\FOREX\FOREX\SYSTEM_LOCK.md` | Production parameter freeze registry. |
| `D:\FOREX\FOREX\lk_system\master_live_bot.py` | Central live engine daemon & interactive CLI control console. |
| `D:\FOREX\FOREX\lk_system\core\apexhunter_engine.py` | ApexHunter v1.2.1 TripleGuard M5 algorithm. |
| `D:\FOREX\FOREX\lk_system\core\risk_guardian.py` | Prop firm circuit breaker, fitted risk, and drawdown guardian. |
| `D:\FOREX\FOREX\lk_system\core\mt5_live_bridge.py` | Microsecond MT5 IPC bridge with connection watchdog. |
| `D:\FOREX\FOREX\lk_system\core\asset_handler.py` | 4-Stage fuzzy broker symbol auto-resolver (`XAUUSD.m`, `GOLD`, etc.). |
| `D:\FOREX\FOREX\lk_system\core\persistence_manager.py`| Atomic state serialization (`bot_runtime_state.json`) and crash recovery. |
| `D:\FOREX\FOREX\lk_system\backtest\unified_backtest_engine.py` | 1-to-1 live parity portfolio backtesting engine. |
| `D:\FOREX\FOREX\lk_system\scratch\` | Validated forensic backtest scripts and MT5 diagnostic utilities. |
| `D:\FOREX\DC\` | Historic reference directory for ApexHunter 1.2 (`nightly_reconciler.py`, `dcc_engine.py`). |

---

## 4. Current State & Account Telemetry

- **Active Broker:** OctaFX-Demo (MetaTrader 5)
- **Account Number:** `213877054`
- **Initial Capital:** `$5,000.00`
- **Current Balance / Equity:** `~$6,511.28` (`+30.2%` all-time gain)
- **Prop Firm Target Status:**
  - Phase 1 Target was `+10.0%` (`+$500.00`).
  - System reached `+$867.88` via Forex SMC on Nov 23, triggering **Target Lock** (protecting the payout/pass from excessive trading).
- **Recent Enhancements (Commit `322b3c5`):**
  - Added CLI flags to `master_live_bot.py` and `run_apexhunter_2.py`:
    `--daily-cb <pct>`, `--max-dd <pct>`, `--floor-cb <pct>`, `--risk <pct>`.
  - Added interactive options `[7]` (Edit Daily DD & CB %) and `[8]` (Edit Max Total DD & Floor CB %) to adjust thresholds live without stopping the bot.

---

## 5. Command Reference Cheat Sheet

### Running Tests
```powershell
# From D:\FOREX\FOREX\lk_system:
python -m unittest discover tests

# Or from D:\FOREX\FOREX:
python -m unittest discover lk_system/tests
```

### Starting the Live Bot
```powershell
# Interactive launcher (From D:\FOREX\FOREX):
python run_apexhunter_2.py

# Live trading with custom risk limits:
python run_apexhunter_2.py --account 213877054 --risk 1.0 --daily-cb 4.5 --max-dd 10.0 --floor-cb 9.0

# Paper trading simulation mode:
python run_apexhunter_2.py --paper --symbols XAUUSD,NAS100,EURUSD,GBPUSD
```

### Running Forensic Single-Day or Multi-Day Backtests
```powershell
# Example: Running exact DC v1.2 reconciler audit on MT5 tick data:
python scratch/run_dc_exact_backtest_nov23_24.py
```

---

## 6. Ready-to-Copy Master Prompt for a New Hydrogen Session

When starting a fresh chat session with **Gemini 4 Hydrogen**, copy and paste the following prompt into the chat window:

```text
Hello Hydrogen. You are now the lead AI engineer and operator for the "ApexHunter 2.0" Institutional Multi-Asset Trading System.

Before taking any actions, read and internalize the master handover document at:
D:\FOREX\FOREX\HYDROGEN_MASTER_HANDOVER.md

Key operating requirements:
1. Workspace root: D:\FOREX\FOREX (Core package in lk_system).
2. Strategy: ApexHunter 2.0 (Gold & Nasdaq on M5 + 6 Major Forex pairs on M15).
3. Purpose: Prop Firm Challenges & Funded Payout Cycles ($5,000 base, 10% Max DD, 4.5% Daily CB, 9.0% Floor CB).
4. Directives:
   - Keep answers concise and direct. Avoid multi-minute speculative reasoning chains.
   - Zero unsolicited architectural overhauls; use surgical diffs only.
   - Always verify code changes with 'python -m unittest discover tests' inside lk_system (100% green required).
5. Current Live Account: OctaFX-Demo #213877054 (Balance ~$6,511.28).

Confirm you have reviewed these directives and state your readiness to proceed.
```
