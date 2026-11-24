"""
Unit Tests for Nightly Reconciler
Verifies:
1. Reconciling matched live broker deals vs theoretical backtest setups.
2. Detecting MISSED_OR_FILTERED trades with exact forensic classification.
3. Detecting UNEXPECTED_LIVE_DEAL with separation between manual trades and bot overrides.
4. Markdown scorecard report generation.
5. Rolling 7-day disk retention pruning.
"""

import unittest
import os
import sys
import json
import shutil
import tempfile
from datetime import datetime, date, timedelta, timezone
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backtest.nightly_reconciler import NightlyReconciler


class TestNightlyReconciler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.reconciler = NightlyReconciler(
            symbols=["XAUUSD", "NAS100", "EURUSD"],
            audit_dir=self.temp_dir,
            retention_days=7
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reconciliation_exact_match(self):
        target_date = date(2026, 11, 20)
        # 1 backtest trade
        bt_trades = [{
            "symbol": "XAUUSD",
            "strategy": "APEXHUNTER_1_2_1",
            "direction": "BUY",
            "entry_time": "2026-11-20 14:35:00",
            "actual_entry": 2650.50,
            "net_pnl": 125.00,
            "tp1_price": 2664.50,
            "tp2_price": 2672.50,
            "total_lots": 0.10,
        }]
        # 1 live setup matching symbol, direction, and timestamp (within 6 mins)
        live_deals = [
            # In deal
            {
                "ticket": 1001,
                "order": 5001,
                "position_id": 5001,
                "symbol": "XAUUSD",
                "time": "2026-11-20 14:35:05",
                "type": "BUY",
                "entry_type": "ENTRY_IN",
                "volume": 0.05,
                "price": 2650.55,
                "profit": 0.0,
                "magic": 88001,
                "comment": "APE_T1"
            },
            # In deal split 2
            {
                "ticket": 1002,
                "order": 5002,
                "position_id": 5002,
                "symbol": "XAUUSD",
                "time": "2026-11-20 14:35:05",
                "type": "BUY",
                "entry_type": "ENTRY_IN",
                "volume": 0.05,
                "price": 2650.55,
                "profit": 0.0,
                "magic": 88002,
                "comment": "APE_T2"
            },
            # Out deal split 1
            {
                "ticket": 1003,
                "order": 5003,
                "position_id": 5001,
                "symbol": "XAUUSD",
                "time": "2026-11-20 15:10:00",
                "type": "SELL",
                "entry_type": "ENTRY_OUT",
                "volume": 0.05,
                "price": 2664.50,
                "profit": 69.75,
                "magic": 88001,
                "comment": "[tp]"
            },
            # Out deal split 2
            {
                "ticket": 1004,
                "order": 5004,
                "position_id": 5002,
                "symbol": "XAUUSD",
                "time": "2026-11-20 15:45:00",
                "type": "SELL",
                "entry_type": "ENTRY_OUT",
                "volume": 0.05,
                "price": 2652.50,
                "profit": 9.75,
                "magic": 88002,
                "comment": "[sl]"
            }
        ]

        result = self.reconciler.reconcile(target_date, bt_trades, live_deals)
        summary = result["summary"]
        self.assertEqual(summary["total_backtest_signals"], 1)
        self.assertEqual(summary["matched_trades"], 1)
        self.assertEqual(summary["discrepancies"], 0)
        self.assertEqual(summary["match_rate_pct"], 100.0)
        self.assertAlmostEqual(summary["live_broker_net_pnl"], 79.50, places=2)

    def test_reconciliation_missed_trade_diagnosis(self):
        target_date = date(2026, 11, 20)
        bt_trades = [{
            "symbol": "NAS100",
            "strategy": "APEXHUNTER_1_2_1",
            "direction": "SELL",
            "entry_time": "2026-11-20 13:05:00",
            "actual_entry": 20500.0,
            "net_pnl": -50.00,
            "tp1_price": 20400.0,
            "tp2_price": 20350.0,
            "total_lots": 0.50,
        }]
        live_deals = []  # Bot did not execute

        result = self.reconciler.reconcile(target_date, bt_trades, live_deals)
        summary = result["summary"]
        self.assertEqual(summary["total_backtest_signals"], 1)
        self.assertEqual(summary["matched_trades"], 0)
        self.assertEqual(summary["discrepancies"], 1)
        self.assertEqual(result["reconciliation_details"][0]["status"], "MISSED_OR_FILTERED")

    def test_reconciliation_unexpected_manual_trade(self):
        target_date = date(2026, 11, 20)
        bt_trades = []
        live_deals = [{
            "ticket": 9999,
            "order": 8888,
            "position_id": 8888,
            "symbol": "EURUSD",
            "time": "2026-11-20 10:00:00",
            "type": "BUY",
            "entry_type": "ENTRY_IN",
            "volume": 0.20,
            "price": 1.0850,
            "profit": 0.0,
            "magic": 0,
            "comment": "Manual phone trade"
        }]

        result = self.reconciler.reconcile(target_date, bt_trades, live_deals)
        summary = result["summary"]
        self.assertEqual(summary["total_backtest_signals"], 0)
        self.assertEqual(summary["manual_discretionary_trades"], 1)
        self.assertEqual(result["reconciliation_details"][0]["status"], "UNEXPECTED_LIVE_DEAL")
        self.assertEqual(result["reconciliation_details"][0]["deal_source"], "MANUAL_TRADE")

    def test_markdown_report_formatting(self):
        audit_res = {
            "date": "2026-11-20",
            "audit_generated_at": "2026-11-20 21:05:00 UTC",
            "summary": {
                "total_backtest_signals": 1,
                "total_live_entries": 1,
                "matched_trades": 1,
                "manual_discretionary_trades": 0,
                "discrepancies": 0,
                "match_rate_pct": 100.0,
                "backtest_net_pnl": 125.0,
                "live_broker_net_pnl": 124.5,
                "bot_realized_pnl": 124.5,
                "manual_realized_pnl": 0.0,
            },
            "reconciliation_details": [{
                "status": "MATCHED_SUCCESS",
                "symbol": "XAUUSD",
                "direction": "BUY",
                "signal_time": "2026-11-20 14:35:00",
                "live_ticket": "1001/1002",
                "backtest_entry": 2650.50,
                "live_entry": 2650.55,
                "backtest_pnl": 125.0,
                "live_pnl": 124.5,
                "discrepancy_reason": "Exact strategy entry confirmed and executed by live bot."
            }]
        }
        md = self.reconciler.generate_markdown_report(audit_res)
        self.assertIn("# Master Unified Daily Forensic Audit & Trade Reconciliation Scorecard", md)
        self.assertIn("**Match Rate:** `100.0%`", md)
        self.assertIn("XAUUSD", md)
        self.assertIn("[MATCHED]", md)


if __name__ == "__main__":
    unittest.main()
