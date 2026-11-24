"""
Master Unified Nightly Forensic Reconciler ("Night Rec")
========================================================
Location: backtest/nightly_reconciler.py

Replays broker ticks & bars through institutional dual-engine rules (DCC ApexHunter v1.2.1
on Gold/Nasdaq and Lewis Kelly SMC on 6 Forex pairs), cross-checks against actual MT5
broker history deals (history_deals_get), identifies slippage and discrepancies with
deep forensic root-cause taxonomy, generates a daily scorecard, alerts Telegram/Discord,
and enforces rolling 7-day disk retention.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time
from datetime import datetime, date, time as dtime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from backtest.unified_backtest_engine import UnifiedBacktestEngine, BacktestResult
from live.notifier import TradeNotifier


class NightlyReconciler:
    """
    Forensic Trade Reconciler for 8-Asset Dual-Engine Trading System.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        audit_dir: Optional[str] = None,
        retention_days: int = 7,
        config_path: Optional[str] = None,
        notifier: Optional[TradeNotifier] = None
    ):
        self.symbols = symbols or [
            "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "USDCAD",
            "XAUUSD", "NAS100"
        ]
        self.retention_days = retention_days
        self.config_path = config_path or os.path.join(PROJECT_ROOT, "live", "config.json")

        if audit_dir is None:
            self.audit_dir = os.path.join(PROJECT_ROOT, "reports", "daily_audits")
        else:
            self.audit_dir = audit_dir
        os.makedirs(self.audit_dir, exist_ok=True)

        self.notifier = notifier or TradeNotifier(config_path=self.config_path)
        self.unified_engine = UnifiedBacktestEngine(
            symbols=self.symbols,
            config_path=self.config_path
        )
        self.broker_offset = self.detect_broker_offset()

    def detect_broker_offset(self) -> timedelta:
        """Detects MT5 broker server timezone offset relative to UTC."""
        if mt5 is not None and mt5.initialize():
            try:
                sample_sym = self.symbols[0]
                rates = mt5.copy_rates_from_pos(sample_sym, mt5.TIMEFRAME_M5, 0, 1)
                if rates is not None and len(rates) > 0:
                    b_time = datetime.fromtimestamp(rates[0]['time'], tz=timezone.utc)
                    now_utc = datetime.now(timezone.utc)
                    diff_hrs = round((b_time - now_utc).total_seconds() / 3600.0)
                    is_fresh = abs((b_time - timedelta(hours=diff_hrs)) - now_utc) < timedelta(minutes=15)
                    if -12 <= diff_hrs <= 14 and is_fresh:
                        return timedelta(hours=diff_hrs)
            except Exception:
                pass
        return timedelta(hours=2)

    def fetch_live_deals(self, target_date: date) -> List[Dict[str, Any]]:
        """Queries MT5 history deals for target date with True UTC conversion."""
        if mt5 is None or not mt5.initialize():
            return []

        utc_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=timezone.utc)
        utc_end = utc_start + timedelta(days=1)
        broker_start = utc_start + self.broker_offset
        broker_end = utc_end + self.broker_offset

        deals = mt5.history_deals_get(broker_start, broker_end)
        if not deals:
            return []

        parsed_deals = []
        for d in deals:
            # Filter non-trades (balance deposits, credits, rollover adjustments)
            if d.entry in (0, 1) and getattr(d, 'type', None) in (0, 1) and d.symbol and d.volume > 0:
                deal_utc = datetime.fromtimestamp(d.time, tz=timezone.utc) - self.broker_offset
                parsed_deals.append({
                    "ticket": d.ticket,
                    "order": d.order,
                    "position_id": getattr(d, "position_id", d.order),
                    "symbol": d.symbol,
                    "time": deal_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "time_posix": d.time - int(self.broker_offset.total_seconds()),
                    "type": "BUY" if d.type == 0 else "SELL",
                    "entry_type": "ENTRY_IN" if d.entry == 0 else "ENTRY_OUT",
                    "volume": round(d.volume, 2),
                    "price": round(d.price, 5),
                    "profit": round(d.profit, 2),
                    "comment": d.comment,
                    "magic": getattr(d, "magic", 0)
                })

        # Multi-day position capture: retrieve missing ENTRY_IN deals for positions that closed today
        known_in_pos = {d["position_id"] for d in parsed_deals if d["entry_type"] == "ENTRY_IN"}
        exit_pos_ids = {d["position_id"] for d in parsed_deals if d["entry_type"] == "ENTRY_OUT"}
        missing_entry_pos = exit_pos_ids - known_in_pos

        for pos_id in missing_entry_pos:
            try:
                pos_deals = mt5.history_deals_get(position=pos_id)
                if pos_deals:
                    for d in pos_deals:
                        if d.entry == 0 and getattr(d, 'type', None) in (0, 1) and d.symbol and d.volume > 0:
                            deal_utc = datetime.fromtimestamp(d.time, tz=timezone.utc) - self.broker_offset
                            parsed_deals.append({
                                "ticket": d.ticket,
                                "order": d.order,
                                "position_id": getattr(d, "position_id", d.order),
                                "symbol": d.symbol,
                                "time": deal_utc.strftime("%Y-%m-%d %H:%M:%S"),
                                "time_posix": d.time - int(self.broker_offset.total_seconds()),
                                "type": "BUY" if d.type == 0 else "SELL",
                                "entry_type": "ENTRY_IN",
                                "volume": round(d.volume, 2),
                                "price": round(d.price, 5),
                                "profit": round(d.profit, 2),
                                "comment": d.comment,
                                "magic": getattr(d, "magic", 0)
                            })
            except Exception:
                pass

        return parsed_deals

    def run_daily_backtest(self, target_date: date) -> List[Dict[str, Any]]:
        """Replays strategy rules for target_date across all symbols."""
        res: BacktestResult = self.unified_engine.run_portfolio_simulation(
            start_date=target_date,
            end_date=target_date
        )
        backtest_trades = []
        for t in res.trades:
            backtest_trades.append({
                "symbol": t.symbol,
                "strategy": t.strategy,
                "direction": t.direction,
                "entry_time": t.entry_time.strftime("%Y-%m-%d %H:%M:%S") if t.entry_time else "",
                "actual_entry": t.entry_price,
                "sl_price": t.sl_price,
                "be_sl_price": t.be_sl_price,
                "tp_levels": t.tp_levels,
                "total_lots": t.total_lots,
                "exit_time": t.exit_time.strftime("%Y-%m-%d %H:%M:%S") if t.exit_time else "",
                "exit_reasons": t.exit_reasons,
                "net_pnl": t.net_pnl,
                "pnl_r": t.pnl_r,
                "tp1_hit": t.tp1_hit,
                "be_hit": t.be_hit,
                "initial_sl_hit": t.initial_sl_hit,
            })
        return backtest_trades

    def reconcile(
        self,
        target_date: date,
        backtest_trades: List[Dict[str, Any]],
        live_deals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Reconciles backtest setups against live MT5 broker deals.
        Categorizes: MATCHED_SUCCESS, MISSED_OR_FILTERED, UNEXPECTED_LIVE_DEAL.
        """
        raw_live_entries = [d for d in live_deals if d["entry_type"] == "ENTRY_IN"]
        exit_deals = [d for d in live_deals if d["entry_type"] == "ENTRY_OUT"]

        # Group split order tickets into cohesive trade setups
        grouped_setups = []
        for d in raw_live_entries:
            d_time = pd.to_datetime(d["time"], utc=True)
            existing = None
            for s in grouped_setups:
                if s["symbol"] == d["symbol"] and s["type"] == d["type"]:
                    s_time = pd.to_datetime(s["time"], utc=True)
                    if abs((d_time - s_time).total_seconds()) <= 60:
                        existing = s
                        break

            pos_id = d.get("position_id", d.get("order", d.get("ticket", 0)))
            order_id = d.get("order", d.get("ticket", 0))
            if existing:
                existing["tickets"].append(d["ticket"])
                existing["orders"].append(order_id)
                existing["position_ids"].append(pos_id)
                existing["volume"] = round(existing.get("volume", 0.0) + d.get("volume", 0.0), 2)
                if d.get("magic"):
                    existing["magics"].append(d["magic"])
                if d.get("comment"):
                    existing["comments"].append(d["comment"])
            else:
                grouped_setups.append({
                    "tickets": [d["ticket"]],
                    "orders": [order_id],
                    "position_ids": [pos_id],
                    "ticket": d["ticket"],
                    "order": order_id,
                    "symbol": d["symbol"],
                    "time": d["time"],
                    "time_posix": d.get("time_posix", 0),
                    "type": d["type"],
                    "entry_type": "ENTRY_IN",
                    "volume": d.get("volume", 0.0),
                    "price": d["price"],
                    "profit": 0.0,
                    "magics": [d.get("magic", 0)],
                    "comments": [d.get("comment", "")] if d.get("comment") else []
                })

        # Calculate realized PnL for each live setup from corresponding exit deals
        for s in grouped_setups:
            s_exit_pnl = sum(
                ed["profit"] for ed in exit_deals
                if ed.get("position_id") in s["position_ids"] or ed.get("order") in s["orders"]
            )
            s["profit"] = round(s_exit_pnl, 2)

        matched_live_indices = set()
        reconciliation_items = []

        # Check each backtest trade against grouped live setups
        for bt in backtest_trades:
            bt_time = pd.to_datetime(bt["entry_time"], utc=True)
            matched_setup = None

            for idx, ld in enumerate(grouped_setups):
                if idx in matched_live_indices:
                    continue
                if ld["symbol"] == bt["symbol"] and ld["type"] == bt["direction"]:
                    ld_time = pd.to_datetime(ld["time"], utc=True)
                    time_diff = abs((ld_time - bt_time).total_seconds())
                    if time_diff <= 360:  # within 6 minutes tolerance
                        matched_setup = ld
                        matched_live_indices.add(idx)
                        break

            if matched_setup:
                ticket_str = "/".join(str(t) for t in matched_setup["tickets"])
                slippage = round(abs(matched_setup["price"] - bt["actual_entry"]), 5)
                reconciliation_items.append({
                    "status": "MATCHED_SUCCESS",
                    "symbol": bt["symbol"],
                    "direction": bt["direction"],
                    "signal_time": bt["entry_time"],
                    "live_ticket": ticket_str,
                    "backtest_entry": bt["actual_entry"],
                    "live_entry": matched_setup["price"],
                    "slippage": slippage,
                    "backtest_pnl": bt["net_pnl"],
                    "live_pnl": matched_setup.get("profit", 0.0),
                    "discrepancy_reason": f"Exact strategy entry confirmed. Fill slippage: {slippage} pts."
                })
            elif any("END_OF_DATA" in str(r) for r in bt.get("exit_reasons", [])):
                reconciliation_items.append({
                    "status": "OPEN_OVERNIGHT",
                    "symbol": bt["symbol"],
                    "direction": bt["direction"],
                    "signal_time": bt["entry_time"],
                    "live_ticket": None,
                    "backtest_entry": bt["actual_entry"],
                    "live_entry": None,
                    "slippage": None,
                    "backtest_pnl": bt["net_pnl"],
                    "live_pnl": 0.0,
                    "discrepancy_reason": "Strategy position entered today and remains open overnight (Targets/Stops not yet reached)."
                })
            else:
                reconciliation_items.append({
                    "status": "MISSED_OR_FILTERED",
                    "symbol": bt["symbol"],
                    "direction": bt["direction"],
                    "signal_time": bt["entry_time"],
                    "live_ticket": None,
                    "backtest_entry": bt["actual_entry"],
                    "live_entry": None,
                    "slippage": None,
                    "backtest_pnl": bt["net_pnl"],
                    "live_pnl": 0.0,
                    "discrepancy_reason": "Setup found in backtest replay but no live broker deal was recorded (Bot was not running, CB cushion was exhausted, or spread spiked at fill)."
                })

        # Check for unexpected live setups (separate bot anomalies from discretionary manual trades)
        for idx, ld in enumerate(grouped_setups):
            if idx not in matched_live_indices:
                ticket_str = "/".join(str(t) for t in ld["tickets"])
                comments_list = ld.get("comments", [])
                magics_list = ld.get("magics", [])
                is_bot = (
                    any(any(k in str(c).upper() for k in ["APE", "DCC", "SMC", "LK", "PFG"]) for c in comments_list) or
                    any(88000 <= m <= 88999 for m in magics_list)
                )
                if is_bot:
                    reason = "Live bot deal executed but not identified by standard strategy backtest (Check manual override or parameter deviation)."
                    deal_source = "BOT_OVERRIDE"
                else:
                    reason = "Manual discretionary trade executed directly on MT5 (External to automated bot strategy)."
                    deal_source = "MANUAL_TRADE"

                reconciliation_items.append({
                    "status": "UNEXPECTED_LIVE_DEAL",
                    "deal_source": deal_source,
                    "symbol": ld["symbol"],
                    "direction": ld["type"],
                    "signal_time": ld["time"],
                    "live_ticket": ticket_str,
                    "backtest_entry": None,
                    "live_entry": ld["price"],
                    "slippage": None,
                    "backtest_pnl": 0.0,
                    "live_pnl": ld.get("profit", 0.0),
                    "discrepancy_reason": reason
                })

        # Scorecard Metrics
        total_bt = len(backtest_trades)
        total_live = len(grouped_setups)
        matched = sum(1 for item in reconciliation_items if item["status"] == "MATCHED_SUCCESS")
        manual_deals = sum(1 for item in reconciliation_items if item.get("deal_source") == "MANUAL_TRADE")
        discrepancies = len(reconciliation_items) - matched
        bt_net_pnl = sum(bt["net_pnl"] for bt in backtest_trades)
        live_net_pnl = sum(d["profit"] for d in live_deals)
        bot_live_pnl = sum(item["live_pnl"] for item in reconciliation_items if item.get("deal_source", "BOT") != "MANUAL_TRADE")
        manual_pnl = sum(item["live_pnl"] for item in reconciliation_items if item.get("deal_source") == "MANUAL_TRADE")

        return {
            "date": target_date.strftime("%Y-%m-%d"),
            "audit_generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "summary": {
                "total_backtest_signals": total_bt,
                "total_live_entries": total_live,
                "matched_trades": matched,
                "manual_discretionary_trades": manual_deals,
                "discrepancies": discrepancies,
                "match_rate_pct": round((matched / total_bt * 100.0) if total_bt > 0 else 100.0, 1),
                "backtest_net_pnl": round(bt_net_pnl, 2),
                "live_broker_net_pnl": round(live_net_pnl, 2),
                "bot_realized_pnl": round(bot_live_pnl, 2),
                "manual_realized_pnl": round(manual_pnl, 2),
            },
            "reconciliation_details": reconciliation_items,
            "backtest_trades": backtest_trades,
            "live_deals": live_deals,
        }

    def generate_markdown_report(self, audit_result: Dict[str, Any]) -> str:
        s = audit_result["summary"]
        dt = audit_result["date"]
        lines = [
            f"# Master Unified Daily Forensic Audit & Trade Reconciliation Scorecard",
            f"**Trading Date:** `{dt}` | **Generated:** `{audit_result['audit_generated_at']}`",
            "",
            "## Summary Metrics",
            f"- **Match Rate:** `{s['match_rate_pct']}%`",
            f"- **Strategy Signals Detected:** `{s['total_backtest_signals']}`",
            f"- **Live Broker Entries:** `{s['total_live_entries']}` (Bot: `{s['total_live_entries'] - s.get('manual_discretionary_trades', 0)}` | Manual: `{s.get('manual_discretionary_trades', 0)}`)",
            f"- **Fully Reconciled Matches:** `{s['matched_trades']}`",
            f"- **Discrepancies Flagged:** `{s['discrepancies']}`",
            f"- **Backtest Net PnL:** `${s['backtest_net_pnl']:,.2f}`",
            f"- **Live Bot Net PnL:** `${s.get('bot_realized_pnl', s['live_broker_net_pnl']):,.2f}`",
            f"- **Manual Discretionary PnL:** `${s.get('manual_realized_pnl', 0.0):,.2f}`",
            f"- **Account Total Realized PnL:** `${s['live_broker_net_pnl']:,.2f}`",
            "",
            "## Reconciliation Breakdown",
            "| Status | Symbol | Dir | Signal Time | Ticket | Backtest PnL | Live PnL | Forensic Diagnosis |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        if not audit_result["reconciliation_details"]:
            lines.append("| `NO_TRADES` | - | - | - | - | $0.00 | $0.00 | No strategy setups or live trades occurred on this date. |")
        else:
            for d in audit_result["reconciliation_details"]:
                if d["status"] == "MATCHED_SUCCESS":
                    status_icon = "[MATCHED]"
                elif d.get("deal_source") == "MANUAL_TRADE":
                    status_icon = "[MANUAL]"
                else:
                    status_icon = "[FLAGGED]"
                ticket_str = str(d["live_ticket"]) if d["live_ticket"] else "N/A"
                lines.append(
                    f"| {status_icon} | **{d['symbol']}** | {d['direction']} | {d['signal_time']} | {ticket_str} | "
                    f"${d['backtest_pnl']:,.2f} | ${d['live_pnl']:,.2f} | {d['discrepancy_reason']} |"
                )

        return "\n".join(lines)

    def save_daily_report(
        self,
        date_str: str,
        report_dict: Dict[str, Any],
        markdown_summary: Optional[str] = None
    ) -> Tuple[str, Optional[str]]:
        """Saves daily JSON scorecard and Markdown summary."""
        os.makedirs(self.audit_dir, exist_ok=True)
        json_filename = f"audit_reconciliation_{date_str}.json"
        json_path = os.path.join(self.audit_dir, json_filename)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, indent=2, default=str)

        md_path = None
        if markdown_summary:
            md_filename = f"audit_reconciliation_{date_str}.md"
            md_path = os.path.join(self.audit_dir, md_filename)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(markdown_summary)

        # Copy to latest
        latest_json = os.path.join(self.audit_dir, "audit_reconciliation_latest.json")
        try:
            shutil.copyfile(json_path, latest_json)
        except Exception:
            pass

        return json_path, md_path

    def prune_aged_reports(self) -> List[str]:
        """Prunes local reports older than retention_days to keep disk lean."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        pruned = []
        for fpath in glob.glob(os.path.join(self.audit_dir, "audit_reconciliation_*.*")):
            if "latest" in fpath:
                continue
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)
                if mtime < cutoff_date:
                    os.remove(fpath)
                    pruned.append(fpath)
            except Exception:
                pass
        return pruned

    def run(self, target_date: date) -> Dict[str, Any]:
        """Main execution flow for target date."""
        print(f"\n================================================================================")
        print(f"  >>> RUNNING MASTER UNIFIED NIGHTLY FORENSIC RECONCILIATION: {target_date} <<<")
        print(f"================================================================================")

        # 1. Backtest Strategy Replay
        print(f"[*] [1/4] Replaying strategy rules for {len(self.symbols)} symbols...")
        bt_trades = self.run_daily_backtest(target_date)
        print(f"[OK] Backtest completed. Setups found: {len(bt_trades)}")

        # 2. Fetch Live MT5 Deals
        print(f"[*] [2/4] Fetching live broker deals from MT5...")
        live_deals = self.fetch_live_deals(target_date)
        print(f"[OK] Live deals retrieved: {len(live_deals)}")

        # 3. Reconcile
        print(f"[*] [3/4] Reconciling backtest setups vs. live broker execution...")
        audit_result = self.reconcile(target_date, bt_trades, live_deals)
        md_report = self.generate_markdown_report(audit_result)

        # 4. Save and manage storage retention
        date_str = target_date.strftime("%Y%m%d")
        json_path, md_path = self.save_daily_report(date_str, audit_result, md_report)
        print(f"[OK] Saved daily audit report to: {json_path}")

        pruned = self.prune_aged_reports()
        if pruned:
            print(f"[Storage] Pruned {len(pruned)} aged report(s) from disk.")

        # 5. Dispatch Notifications
        s = audit_result["summary"]
        bot_deals_cnt = s['total_live_entries'] - s.get('manual_discretionary_trades', 0)
        manual_cnt = s.get('manual_discretionary_trades', 0)
        alert_text = (
            f"<b>🌙 Master Unified Nightly Audit Scorecard ({target_date})</b>\n\n"
            f"• <b>Match Rate:</b> {s['match_rate_pct']}%\n"
            f"• <b>Strategy Setups:</b> {s['total_backtest_signals']}\n"
            f"• <b>Live Broker Deals:</b> {s['total_live_entries']} (Bot: {bot_deals_cnt} | Manual: {manual_cnt})\n"
            f"• <b>Reconciled Matches:</b> {s['matched_trades']}\n"
            f"• <b>Discrepancies:</b> {s['discrepancies']}\n"
            f"• <b>Backtest PnL:</b> ${s['backtest_net_pnl']:,.2f}\n"
            f"• <b>Live Bot PnL:</b> ${s.get('bot_realized_pnl', s['live_broker_net_pnl']):,.2f}\n"
            f"• <b>Manual PnL:</b> ${s.get('manual_realized_pnl', 0.0):,.2f}\n"
            f"• <b>Account Total PnL:</b> ${s['live_broker_net_pnl']:,.2f}\n\n"
            f"<i>Detailed scorecard saved to reports/daily_audits/audit_reconciliation_{date_str}.json</i>"
        )
        self.notifier.notify_nightly_audit(
            title=f"🌙 Master Unified Nightly Audit Scorecard ({target_date})",
            summary_text=alert_text
        )

        print("\n" + md_report + "\n")
        print("================================================================================\n")
        return audit_result


def main():
    parser = argparse.ArgumentParser(description="Master Unified Nightly Forensic Reconciliation Engine")
    parser.add_argument("--date", type=str, help="Target audit date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--today", action="store_true", help="Audit today's trading session")
    parser.add_argument("--yesterday", action="store_true", help="Audit yesterday's trading session")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to audit (default: all 8)")
    parser.add_argument("--retention-days", type=int, default=7, help="Rolling local storage retention in days")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    if args.yesterday:
        target_date = (now_utc - timedelta(days=1)).date()
    elif args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = now_utc.date()

    reconciler = NightlyReconciler(symbols=args.symbols, retention_days=args.retention_days)
    reconciler.run(target_date)


if __name__ == "__main__":
    main()
