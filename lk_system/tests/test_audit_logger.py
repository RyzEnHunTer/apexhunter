"""
Unit Tests for Institutional MarketAuditLogger & EngineLogger
=============================================================
Tests:
1. Log directory creation and path resolution
2. Structured candle calculation CSV logging (Headers, rows, daily naming)
3. Human-readable surveillance event logging
4. Engine logger rotating log stream (INFO, WARN, ERROR, TRADE)
5. Live state JSON export and schema validation
"""

import os
import sys
import json
import shutil
import unittest
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.audit_logger import MarketAuditLogger, EngineLogger, LiveStateExporter

class TestAuditLogger(unittest.TestCase):
    def setUp(self):
        self.test_log_dir = os.path.join(PROJECT_ROOT, "logs", "_test_temp_logs")
        if os.path.exists(self.test_log_dir):
            shutil.rmtree(self.test_log_dir, ignore_errors=True)
        os.makedirs(self.test_log_dir, exist_ok=True)

        self.audit_logger = MarketAuditLogger(log_dir=self.test_log_dir)
        self.engine_logger = EngineLogger(log_dir=self.test_log_dir)
        self.state_exporter = LiveStateExporter(log_dir=self.test_log_dir)

    def tearDown(self):
        if os.path.exists(self.test_log_dir):
            shutil.rmtree(self.test_log_dir, ignore_errors=True)

    def test_log_candle_apex_record(self):
        """Verifies ApexHunter M5 candle calculation logging to CSV and text log."""
        record = {
            "timestamp_utc": "2026-11-19 12:00:00",
            "timestamp_ist": "2026-11-19 17:30:00",
            "symbol": "XAUUSD",
            "strategy": "APEXHUNTER_1_2_1",
            "timeframe": "M5",
            "open": 2650.0,
            "high": 2655.0,
            "low": 2648.0,
            "close": 2654.0,
            "volume": 120,
            "ema9_5m": 2652.1,
            "ema20_5m": 2650.4,
            "ema_gap": 1.7,
            "vwap_5m": 2651.5,
            "bias_1h": 1,
            "ema9_1h": 2645.0,
            "ema20_1h": 2640.0,
            "adx_1h": 25.4,
            "atr_1h": 8.5,
            "h1_ema20_level": 2640.0,
            "stretch_ratio": 1.64,
            "chase_ratio": 0.42,
            "sweep_detected": True,
            "sweep_level": 2648.0,
            "swept_pts": 2.5,
            "decision": "SIGNAL_BUY",
            "reason": "M5 Bullish sweep with 1H EMA alignment",
            "planned_entry": 2654.0,
            "planned_sl": 2646.35,
            "planned_tp1": 2664.71,
            "planned_tp2": 2670.06,
            "planned_lots": 0.15
        }
        self.audit_logger.log_candle(record)

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        csv_file = os.path.join(self.test_log_dir, f"market_calculations_{date_str}.csv")
        txt_file = os.path.join(self.test_log_dir, f"market_surveillance_{date_str}.log")

        self.assertTrue(os.path.exists(csv_file), "CSV calculation log must exist")
        self.assertTrue(os.path.exists(txt_file), "Text surveillance log must exist")

        with open(csv_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 2)  # Header + 1 record
            self.assertIn("XAUUSD", lines[1])
            self.assertIn("APEXHUNTER_1_2_1", lines[1])
            self.assertIn("SIGNAL_BUY", lines[1])

        with open(txt_file, 'r', encoding='utf-8') as f:
            txt_content = f.read()
            self.assertIn("XAUUSD", txt_content)
            self.assertIn("SIGNAL_BUY", txt_content)

    def test_log_candle_lewis_kelly_record(self):
        """Verifies Lewis Kelly SMC M15 candle calculation logging."""
        record = {
            "timestamp_utc": "2026-11-19 08:15:00",
            "timestamp_ist": "2026-11-19 13:45:00",
            "symbol": "EURUSD",
            "strategy": "LEWIS_KELLY_SMC",
            "timeframe": "M15",
            "open": 1.08500,
            "high": 1.08700,
            "low": 1.08250,
            "close": 1.08380,
            "volume": 350,
            "asian_high": 1.08700,
            "asian_low": 1.08300,
            "asian_range_pips": 40.0,
            "session_active": "London",
            "sweep_detected": True,
            "sweep_level": 1.08300,
            "swept_pts": 5.0,
            "decision": "SIGNAL_BUY",
            "reason": "Bullish Asian Low Sweep confirmed",
            "planned_entry": 1.08380,
            "planned_sl": 1.08200,
            "planned_tp1": 1.08830,
            "planned_tp2": 1.09280,
            "planned_tp3": 1.09820,
            "planned_lots": 0.28
        }
        self.audit_logger.log_candle(record)

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        csv_file = os.path.join(self.test_log_dir, f"market_calculations_{date_str}.csv")
        txt_file = os.path.join(self.test_log_dir, f"market_surveillance_{date_str}.log")

        with open(csv_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("EURUSD", lines[1])
            self.assertIn("LEWIS_KELLY_SMC", lines[1])

        with open(txt_file, 'r', encoding='utf-8') as f:
            txt_content = f.read()
            self.assertIn("EURUSD", txt_content)
            self.assertIn("LEWIS_KELLY_SMC", txt_content)
            self.assertIn("1.08300", txt_content)

    def test_engine_logger(self):
        """Verifies system execution logging (INFO, TRADE, WARN, ERROR)."""
        self.engine_logger.info("Bot initialized successfully on OctaFX-Demo")
        self.engine_logger.trade("Order placed: #123456 EURUSD 0.10 lots @ 1.08380")
        self.engine_logger.warning("Spread temporarily elevated: 2.5p")
        self.engine_logger.error("Simulation test error caught", exc_info=False)

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        eng_file = os.path.join(self.test_log_dir, f"live_engine_{date_str}.log")
        self.assertTrue(os.path.exists(eng_file), "Engine log file must exist")

        with open(eng_file, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn("[INFO]", content)
            self.assertIn("[TRADE]", content)
            self.assertIn("[WARNING]", content)
            self.assertIn("[ERROR]", content)
            self.assertIn("#123456", content)

    def test_live_state_exporter(self):
        """Verifies JSON state dump for external dashboards & monitoring."""
        state = {
            "status": "HUNTING",
            "account_id": "213877054",
            "balance": 5778.08,
            "equity": 5778.08,
            "daily_cb_room": 231.12,
            "floor_cb_room": 1203.08,
            "active_slots": 0,
            "max_slots": 4,
            "target_locked": False,
            "last_audit_symbol": "XAUUSD"
        }
        self.state_exporter.export_state(state)

        json_file = os.path.join(self.test_log_dir, "live_state.json")
        self.assertTrue(os.path.exists(json_file), "live_state.json must exist")

        with open(json_file, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
            self.assertEqual(loaded["account_id"], "213877054")
            self.assertEqual(loaded["max_slots"], 4)
            self.assertIn("updated_utc", loaded)

if __name__ == '__main__':
    unittest.main()
