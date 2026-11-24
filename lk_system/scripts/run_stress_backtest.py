"""
Institutional Dual-Engine Stress Test & Forensic Backtest Runner
================================================================
Location: scripts/run_stress_backtest.py

Runs historical backtests & stress tests with 100% logic parity to master_live_bot.py:
- Simulates all 8 symbols concurrently:
  * Gold (XAUUSD) & Nasdaq (NAS100) via DCC ApexHunter v1.2.1
  * 6 Forex Pairs (EURUSD, GBPUSD, USDJPY, AUDUSD, EURJPY, USDCAD) via Lewis Kelly SMC
- Evaluates Breakeven buffer (+0.2R) on TP1 hit for 2-split and 3-split orders.
- Enforces Dual Circuit Breakers (4.0% daily CB, 8.5% floor CB).
- Enforces Concurrency caps (max 4 concurrent, max 1 per symbol, max 2 losses per symbol/day).
- Exports full trade log CSV and summary JSON.

Usage:
  python scripts/run_stress_backtest.py --days 14
  python scripts/run_stress_backtest.py --start 2026-11-01 --end 2026-11-20
  python scripts/run_stress_backtest.py --symbols XAUUSD NAS100 --capital 5000
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, date, timedelta, timezone
from typing import List, Optional

import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtest.unified_backtest_engine import UnifiedBacktestEngine, BacktestResult

console = Console()


def format_dollar(val: float) -> str:
    sign = "+" if val >= 0 else "-"
    return f"{sign}${abs(val):,.2f}"


def run_stress_backtest(
    start_date: date,
    end_date: date,
    symbols: Optional[List[str]] = None,
    capital: float = 5000.0,
    risk: float = 0.01,
    export_csv: Optional[str] = None,
    export_json: Optional[str] = None
) -> BacktestResult:
    target_symbols = symbols or [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "USDCAD",
        "XAUUSD", "NAS100"
    ]

    console.print(Panel(
        f"[bold bright_green]MASTER INSTITUTIONAL DUAL-ENGINE BACKTEST & STRESS TEST[/]\n"
        f"Period: [cyan]{start_date}[/] to [cyan]{end_date}[/] ({(end_date - start_date).days + 1} days)\n"
        f"Symbols ({len(target_symbols)}): [yellow]{', '.join(target_symbols)}[/]\n"
        f"Capital: [bold bright_white]${capital:,.2f}[/] | Risk: [bold bright_white]{risk*100:.1f}%[/] | Max Slots: [bold bright_white]4[/]\n"
        f"Dual Circuit Breakers: [red]4.0% Daily CB[/] | [red]8.5% Floor CB ($4,575)[/]",
        title="[bold bright_cyan]SIMULATION CONFIGURATION[/]",
        border_style="bright_blue"
    ))

    engine = UnifiedBacktestEngine(
        symbols=target_symbols,
        base_capital=capital,
        risk_per_trade=risk,
        max_concurrent_trades=4,
        max_daily_losses_per_symbol=2,
        daily_cb_pct=0.040,
        max_floor_cb_pct=0.085,
    )

    console.print("[dim]Executing chronological simulation across symbols...[/]")
    result: BacktestResult = engine.run_portfolio_simulation(start_date, end_date)
    s = result.summary

    # Summary table
    table = Table(title="Portfolio Performance Summary", border_style="bright_cyan")
    table.add_column("Metric", style="bold white")
    table.add_column("Value", style="bold bright_green")

    pnl_style = "bold bright_green" if s["net_pnl"] >= 0 else "bold red"
    table.add_row("Total Trades", str(s["total_trades"]))
    table.add_row("Win Rate", f"{s['win_rate_pct']:.1f}% ({s['winning_trades']} W / {s['losing_trades']} L)")
    table.add_row("Profit Factor", f"{s['profit_factor']:.2f}")
    table.add_row("Net Realized PnL", f"[{pnl_style}]{format_dollar(s['net_pnl'])} ({s['net_pnl_pct']:+.2f}%)[/]")
    table.add_row("Final Balance", f"${s['final_balance']:,.2f}")
    table.add_row("Max Peak-to-Trough Drawdown", f"-${s['max_drawdown_usd']:,.2f} (-{s['max_drawdown_pct']:.2f}%)")
    table.add_row("Total Filtered / Blocked Setups", str(s["total_rejected_setups"]))

    console.print(table)

    # Per-Symbol Breakdown
    if result.trades:
        sym_table = Table(title="Per-Symbol Strategy Breakdown", border_style="bright_yellow")
        sym_table.add_column("Symbol", style="bold cyan")
        sym_table.add_column("Strategy", style="dim")
        sym_table.add_column("Trades", justify="right")
        sym_table.add_column("Win Rate", justify="right")
        sym_table.add_column("Net PnL ($)", justify="right")
        sym_table.add_column("TP1 Hit Rate", justify="right")

        for sym in target_symbols:
            sym_trades = [t for t in result.trades if t.symbol == sym]
            if not sym_trades:
                continue
            strat = sym_trades[0].strategy
            w = sum(1 for t in sym_trades if t.net_pnl > 0)
            wr = (w / len(sym_trades) * 100.0) if sym_trades else 0.0
            pnl = sum(t.net_pnl for t in sym_trades)
            tp1_rate = (sum(1 for t in sym_trades if t.tp1_hit) / len(sym_trades) * 100.0) if sym_trades else 0.0
            p_style = "bright_green" if pnl >= 0 else "red"

            sym_table.add_row(
                sym,
                strat,
                str(len(sym_trades)),
                f"{wr:.1f}%",
                f"[{p_style}]{format_dollar(pnl)}[/]",
                f"{tp1_rate:.1f}%"
            )

        console.print(sym_table)

    # Export
    if export_csv:
        result.export_csv(export_csv)
        console.print(f"[bold bright_green][OK][/] Trade log exported to: [cyan]{export_csv}[/]")

    if export_json:
        os.makedirs(os.path.dirname(os.path.abspath(export_json)), exist_ok=True)
        import json
        with open(export_json, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        console.print(f"[bold bright_green][OK][/] Summary exported to: [cyan]{export_json}[/]")

    return result


def main():
    parser = argparse.ArgumentParser(description="Institutional Dual-Engine Stress Test & Forensic Backtest Runner")
    parser.add_argument("--days", type=int, default=14, help="Number of trailing days to backtest (default: 14)")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--symbols", nargs="+", help="Symbols to test (default: all 8)")
    parser.add_argument("--capital", type=float, default=5000.0, help="Initial capital (default: $5,000)")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (default: 0.01)")
    parser.add_argument("--export-csv", type=str, default="reports/stress_test_trades.csv", help="CSV export path")
    parser.add_argument("--export-json", type=str, default="reports/stress_test_summary.json", help="JSON export path")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    if args.start and args.end:
        start_d = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_d = datetime.strptime(args.end, "%Y-%m-%d").date()
    else:
        end_d = now_utc.date()
        start_d = end_d - timedelta(days=args.days)

    run_stress_backtest(
        start_date=start_d,
        end_date=end_d,
        symbols=args.symbols,
        capital=args.capital,
        risk=args.risk,
        export_csv=args.export_csv,
        export_json=args.export_json
    )


if __name__ == "__main__":
    main()
