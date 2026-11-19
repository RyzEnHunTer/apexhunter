"""
Unit Tests for Rich Live Surveillance Animation & Candle Countdown Engine
=========================================================================
Location: tests/test_surveillance_display.py
"""

import unittest
from datetime import datetime, timezone, timedelta
import os
import sys
from unittest.mock import MagicMock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from master_live_bot import MasterUnifiedBot
from rich.text import Text

class TestSurveillanceDisplay(unittest.TestCase):
    def setUp(self):
        self.bot = MasterUnifiedBot(dry_run=True, base_capital=5000.0)

    def test_candle_countdown_math(self):
        """Tests that M5 and M15 countdowns calculate exact remaining minutes and seconds."""
        # 14:02:45 UTC -> 2 min 45 sec into 5m bar (165s elapsed, 135s left = 02m 15s)
        # 14:02:45 UTC -> 2 min 45 sec into 15m bar (165s elapsed, 735s left = 12m 15s)
        test_dt = datetime(2026, 3, 10, 14, 2, 45, tzinfo=timezone.utc)
        cd_5m, cd_15m = self.bot.calculate_candle_countdowns(test_dt)

        self.assertAlmostEqual(cd_5m['seconds_left'], 135.0, delta=0.5)
        self.assertEqual(cd_5m['minutes'], 2)
        self.assertEqual(cd_5m['seconds'], 15)
        self.assertEqual(cd_5m['color_style'], "bold green")

        self.assertAlmostEqual(cd_15m['seconds_left'], 735.0, delta=0.5)
        self.assertEqual(cd_15m['minutes'], 12)
        self.assertEqual(cd_15m['seconds'], 15)
        self.assertEqual(cd_15m['color_style'], "bold green")

    def test_candle_countdown_urgency_colors(self):
        """Tests color changes as candle close approaches (<60s yellow, <=30s bright_red)."""
        # 14:04:10 UTC -> 50s left in M5 (should be bold yellow)
        dt_yellow = datetime(2026, 3, 10, 14, 4, 10, tzinfo=timezone.utc)
        cd_5m_y, _ = self.bot.calculate_candle_countdowns(dt_yellow)
        self.assertEqual(cd_5m_y['color_style'], "bold yellow")
        self.assertEqual(cd_5m_y['seconds'], 50)

        # 14:04:45 UTC -> 15s left in M5 (should be bold bright_red for imminent close)
        dt_red = datetime(2026, 3, 10, 14, 4, 45, tzinfo=timezone.utc)
        cd_5m_r, _ = self.bot.calculate_candle_countdowns(dt_red)
        self.assertEqual(cd_5m_r['color_style'], "bold bright_red")
        self.assertEqual(cd_5m_r['seconds'], 15)

    def test_spinner_animation_progression(self):
        """Tests that rotating radar spinner advances frames and colors on each tick."""
        initial_idx = self.bot.spinner_idx
        f1 = self.bot.get_spinner_frame()
        self.assertEqual(self.bot.spinner_idx, initial_idx + 1)
        f2 = self.bot.get_spinner_frame()
        self.assertEqual(self.bot.spinner_idx, initial_idx + 2)
        self.assertNotEqual(f1, f2)

    def test_build_surveillance_status_text(self):
        """Tests that build_surveillance_status creates a valid Rich Text line."""
        acc_mock = MagicMock()
        acc_mock.login = 213877054
        acc_mock.server = "OctaFX-Demo"
        acc_mock.balance = 5778.08
        acc_mock.equity = 5778.08

        now_utc = datetime(2026, 3, 10, 14, 2, 0, tzinfo=timezone.utc)
        text_obj = self.bot.build_surveillance_status(now_utc, acc_mock)

        self.assertIsInstance(text_obj, Text)
        plain_str = text_obj.plain
        self.assertIn("SURVEILLANCE", plain_str)
        self.assertIn("UTC", plain_str)
        self.assertIn("IST", plain_str)
        self.assertIn("M5", plain_str)
        self.assertIn("M15", plain_str)
        self.assertIn("Eq: $5,778.08", plain_str)

if __name__ == '__main__':
    unittest.main()
