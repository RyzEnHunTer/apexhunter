import unittest
from datetime import datetime, timezone, timedelta
import os
import sys

# Add lk_system root
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.risk_guardian import RiskGuardian

class TestRiskGuardian(unittest.TestCase):
    def setUp(self):
        self.guardian = RiskGuardian(
            baseline_capital=5000.0,
            daily_cb_pct=0.040,      # 4.0% ($200)
            max_floor_cb_pct=0.085,  # 8.5% ($425)
            hard_floor_pct=0.100,    # 10.0% ($500)
            normal_risk_pct=0.010,   # 1.0% ($50)
            recovery_risk_pct=0.002  # 0.2% ($10)
        )
        
    def test_daily_anchor_reset(self):
        t1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t1, current_balance=5050.0)
        self.assertEqual(self.guardian.day_start_balance, 5050.0)
        self.assertFalse(self.guardian.daily_halt_active)
        
        # Advance time to next day 00:05 UTC
        t2 = datetime(2026, 1, 16, 0, 5, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t2, current_balance=5120.0)
        self.assertEqual(self.guardian.day_start_balance, 5120.0)
        self.assertFalse(self.guardian.daily_halt_active)

    def test_daily_circuit_breaker_halt(self):
        t1 = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t1, current_balance=5000.0)
        
        # Loss of $210 (4.2% > 4.0% limit)
        allowed, reason = self.guardian.is_trading_allowed(current_balance=4790.0, current_equity=4790.0)
        self.assertFalse(allowed)
        self.assertIn("Daily Circuit Breaker", reason)
        self.assertTrue(self.guardian.daily_halt_active)
        
        # Risk calculation should return 0.0
        risk = self.guardian.calculate_fitted_risk_usd(current_balance=4790.0, current_equity=4790.0)
        self.assertEqual(risk, 0.0)

    def test_fitted_risk_near_daily_cb(self):
        t1 = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t1, current_balance=5000.0)
        
        # Current daily loss is $165 (remains $35 before $200 limit)
        # Normal risk would be $50, but it should be clamped to $35!
        risk = self.guardian.calculate_fitted_risk_usd(current_balance=4835.0, current_equity=4835.0)
        self.assertAlmostEqual(risk, 35.0, delta=0.5)

    def test_fitted_risk_near_floor_cb(self):
        t1 = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t1, current_balance=4600.0)
        
        # Baseline is $5,000, 8.5% CB is at $4,575.
        # Current equity is $4,600 (floor drop = $400, remains $25 before $425 line)
        # Normal risk would be $46, but it should be clamped to $25!
        risk = self.guardian.calculate_fitted_risk_usd(current_balance=4600.0, current_equity=4600.0)
        self.assertAlmostEqual(risk, 25.0, delta=1.0)

    def test_recovery_buffer_risk_scaling(self):
        t1 = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t1, current_balance=4550.0)
        
        # Account is inside 1.5% recovery buffer ($4,550 < $4,575)
        # Risk should scale down to 0.20% (~$9.10)
        risk = self.guardian.calculate_fitted_risk_usd(current_balance=4550.0, current_equity=4550.0)
        self.assertAlmostEqual(risk, 4550.0 * 0.002, delta=0.5)

    def test_exact_lot_size_calculation(self):
        # EURUSD: tick_size=0.00001, tick_value=1.0, SL=15 pips (0.00150), Risk=$50.00
        lots = self.guardian.calculate_lot_size(
            symbol="EURUSD",
            entry_price=1.08500,
            sl_price=1.08350,
            tick_size=0.00001,
            tick_value=1.0000,
            volume_min=0.01,
            volume_step=0.01,
            risk_usd=50.0
        )
        self.assertAlmostEqual(lots, 0.33, delta=0.02)
        
        # USDJPY: tick_size=0.001, tick_value=0.6278, SL=20 pips (0.200), Risk=$50.00
        lots_jpy = self.guardian.calculate_lot_size(
            symbol="USDJPY",
            entry_price=155.000,
            sl_price=154.800,
            tick_size=0.001,
            tick_value=0.6278,
            volume_min=0.01,
            volume_step=0.01,
            risk_usd=50.0
        )
        self.assertAlmostEqual(lots_jpy, 0.40, delta=0.02)

if __name__ == '__main__':
    unittest.main()
