import unittest
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.spread_guardian import SpreadGuardian

class TestSpreadGuardian(unittest.TestCase):
    def setUp(self):
        self.guardian = SpreadGuardian(
            max_spread_sl_ratio=0.30,
            custom_caps={
                'EURUSD': 2.50,
                'GBPUSD': 3.00,
                'USDJPY': 3.50,
                'XAUUSD': 1.00,
                'NAS100': 6.50
            }
        )

    def test_normal_spread_approved(self):
        # EURUSD: Normal 1.2 pip spread on a 10 pip SL (1.2 / 10 = 12% < 30%)
        ok, reason = self.guardian.is_spread_acceptable(
            symbol='EURUSD',
            live_ask=1.08512,
            live_bid=1.08500,
            entry_price=1.08512,
            sl_price=1.08412 # 10 pips SL
        )
        self.assertTrue(ok)
        self.assertIn("Spread OK", reason)

    def test_spread_exceeds_absolute_cap_rejected(self):
        # EURUSD: Rollover blowout spread of 3.2 pips (> 2.50 cap) on 20 pip SL
        ok, reason = self.guardian.is_spread_acceptable(
            symbol='EURUSD',
            live_ask=1.08532,
            live_bid=1.08500,
            entry_price=1.08532,
            sl_price=1.08332 # 20 pips SL
        )
        self.assertFalse(ok)
        self.assertIn("exceeds absolute max cap", reason)

    def test_spread_exceeds_sl_ratio_rejected(self):
        # EURUSD: Spread is 2.0 pips (below 2.50 cap), BUT SL distance is only 5 pips!
        # Ratio = 2.0 / 5.0 = 40% > 30% ratio limit
        ok, reason = self.guardian.is_spread_acceptable(
            symbol='EURUSD',
            live_ask=1.08520,
            live_bid=1.08500,
            entry_price=1.08520,
            sl_price=1.08470 # 5 pips SL
        )
        self.assertFalse(ok)
        self.assertIn("exceeds 30.0% limit", reason)

    def test_gold_and_nasdaq_spread_check(self):
        # Gold: Spread $0.40 on $3.00 SL -> Ratio = 0.40 / 3.00 = 13.3% < 30% -> OK
        ok_gold, _ = self.guardian.is_spread_acceptable(
            symbol='XAUUSD',
            live_ask=2700.40,
            live_bid=2700.00,
            entry_price=2700.40,
            sl_price=2697.40
        )
        self.assertTrue(ok_gold)

        # Nasdaq: Spread 8.5 pts (> 6.50 cap) -> Rejected
        ok_nas, reason_nas = self.guardian.is_spread_acceptable(
            symbol='NAS100',
            live_ask=20508.5,
            live_bid=20500.0,
            entry_price=20508.5,
            sl_price=20480.0
        )
        self.assertFalse(ok_nas)
        self.assertIn("exceeds absolute max cap", reason_nas)

if __name__ == '__main__':
    unittest.main()
