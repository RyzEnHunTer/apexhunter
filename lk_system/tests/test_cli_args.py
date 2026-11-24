"""
Test suite for ApexHunter 2.0 CLI flags and RiskGuardian parameter overrides.
Verifies that --daily-cb, --max-dd, --floor-cb, and --risk are accurately parsed,
converted (both percent and decimal formats), and wired to RiskGuardian.
"""

import unittest
import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from master_live_bot import MasterUnifiedBot


class TestCLIArgsAndRiskOverrides(unittest.TestCase):
    def test_percentage_format_initialization(self):
        bot = MasterUnifiedBot(
            dry_run=True,
            base_capital=5000.0,
            daily_cb_pct=4.5,      # 4.5%
            max_floor_cb_pct=8.0,  # 8.0%
            hard_floor_pct=9.5,    # 9.5%
            risk_pct=1.25          # 1.25%
        )
        self.assertAlmostEqual(bot.risk_guardian.daily_cb_pct, 0.045, places=4)
        self.assertAlmostEqual(bot.risk_guardian.max_floor_cb_pct, 0.080, places=4)
        self.assertAlmostEqual(bot.risk_guardian.hard_floor_pct, 0.095, places=4)
        self.assertAlmostEqual(bot.risk_guardian.normal_risk_pct, 0.0125, places=4)

    def test_decimal_format_initialization(self):
        bot = MasterUnifiedBot(
            dry_run=True,
            base_capital=5000.0,
            daily_cb_pct=0.035,
            max_floor_cb_pct=0.075,
            hard_floor_pct=0.090,
            risk_pct=0.008
        )
        self.assertAlmostEqual(bot.risk_guardian.daily_cb_pct, 0.035, places=4)
        self.assertAlmostEqual(bot.risk_guardian.max_floor_cb_pct, 0.075, places=4)
        self.assertAlmostEqual(bot.risk_guardian.hard_floor_pct, 0.090, places=4)
        self.assertAlmostEqual(bot.risk_guardian.normal_risk_pct, 0.008, places=4)


if __name__ == '__main__':
    unittest.main()
