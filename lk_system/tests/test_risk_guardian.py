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

    def test_update_parameters_dynamic(self):
        t1 = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t1, current_balance=5000.0)
        self.assertEqual(self.guardian.daily_drawdown_floor, 4800.0) # 5000 - (5000 * 0.04)

        # Update daily CB to 5% (0.05) and hard floor to 12% (0.12)
        self.guardian.update_parameters(
            daily_cb_pct=5.0, # passed as percent
            max_floor_cb_pct=0.090, # passed as decimal
            hard_floor_pct=12.0, # passed as percent
            normal_risk_pct=0.015 # passed as decimal
        )
        self.assertAlmostEqual(self.guardian.daily_cb_pct, 0.05, places=4)
        self.assertAlmostEqual(self.guardian.daily_drawdown_floor, 4750.0, places=2) # 5000 - (5000 * 0.05)
        self.assertAlmostEqual(self.guardian.max_floor_cb_pct, 0.09, places=4)
        self.assertAlmostEqual(self.guardian.hard_floor_pct, 0.12, places=4)
        self.assertAlmostEqual(self.guardian.normal_risk_pct, 0.015, places=4)

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

    def test_consecutive_losses_per_symbol_halt(self):
        t1 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t1, current_balance=5000.0)

        # 1st loss on NAS100
        self.guardian.record_trade_result(symbol="NAS100", is_loss=True)
        allowed, reason = self.guardian.is_symbol_trading_allowed("NAS100", 4950.0, 4950.0)
        self.assertTrue(allowed)
        self.assertEqual(self.guardian.symbol_consecutive_losses.get("NAS100", 0), 1)

        # 2nd consecutive loss on NAS100 -> Halts NAS100 for the remainder of the day!
        self.guardian.record_trade_result(symbol="NAS100", is_loss=True)
        allowed, reason = self.guardian.is_symbol_trading_allowed("NAS100", 4900.0, 4900.0)
        self.assertFalse(allowed)
        self.assertIn("Halted", reason)
        self.assertIn("consecutive", reason.lower())

        # Other symbols like EURUSD or XAUUSD should remain allowed (symbol isolation)
        allowed_gold, _ = self.guardian.is_symbol_trading_allowed("XAUUSD", 4900.0, 4900.0)
        self.assertTrue(allowed_gold)

        # A win on EURUSD resets its consecutive counter
        self.guardian.record_trade_result(symbol="EURUSD", is_loss=True)
        self.assertEqual(self.guardian.symbol_consecutive_losses.get("EURUSD", 0), 1)
        self.guardian.record_trade_result(symbol="EURUSD", is_loss=False)
        self.assertEqual(self.guardian.symbol_consecutive_losses.get("EURUSD", 0), 0)

        # Next day rollover unhalts NAS100
        t2 = datetime(2026, 11, 20, 0, 5, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t2, current_balance=4900.0)
        allowed_next_day, _ = self.guardian.is_symbol_trading_allowed("NAS100", 4900.0, 4900.0)
        self.assertTrue(allowed_next_day)
        self.assertEqual(self.guardian.symbol_consecutive_losses.get("NAS100", 0), 0)

    def test_overnight_holding_floating_profit_anchor(self):
        # The5ers / FTMO rule: at 00:00 server time, anchor is max(balance, equity)
        # Starting base capital = $5,000, 4.0% daily limit ($200)
        # Overnight runner in floating profit: balance=$5,000, equity=$5,150 (+150 floating profit)
        t_reset = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
        rollover = self.guardian.reset_daily_if_new_day(t_reset, current_balance=5000.0, current_equity=5150.0)
        self.assertTrue(rollover)

        # Anchor must be $5,150 (higher of balance and equity)
        self.assertEqual(self.guardian.day_start_balance, 5000.0)
        self.assertEqual(self.guardian.day_start_equity, 5150.0)
        self.assertEqual(self.guardian.day_start_anchor, 5150.0)

        # Daily loss threshold floor is $5,150 - $200 = $4,950
        self.assertEqual(self.guardian.daily_drawdown_floor, 4950.0)

        # 1. Floating profit drops by $50 (equity drops to $5,100) -> Allowed!
        allowed, _ = self.guardian.is_trading_allowed(current_balance=5000.0, current_equity=5100.0)
        self.assertTrue(allowed)
        rem_risk = self.guardian.calculate_fitted_risk_usd(current_balance=5000.0, current_equity=5100.0)
        # Remaining room to $4,950 floor is $5,000 - $4,950 = $50
        self.assertAlmostEqual(rem_risk, 50.0, delta=1.0)

        # 2. Floating profit pulls back to BE (+0.2R / +$10) and closes at $5,010
        allowed, _ = self.guardian.is_trading_allowed(current_balance=5010.0, current_equity=5010.0)
        self.assertTrue(allowed)
        rem_risk = self.guardian.calculate_fitted_risk_usd(current_balance=5010.0, current_equity=5010.0)
        # Normal 1% risk on $5,010 is $50.10, which fits inside $60 room
        self.assertAlmostEqual(rem_risk, 50.10, delta=1.0)

        # 3. If equity drops to $4,980, remaining room to $4,950 floor is $30 -> Risk clamped to $30!
        clamped_risk = self.guardian.calculate_fitted_risk_usd(current_balance=4980.0, current_equity=4980.0)
        self.assertAlmostEqual(clamped_risk, 30.0, delta=1.0)

        # 4. If equity falls below $4,950 floor -> Daily CB Triggers!
        allowed, reason = self.guardian.is_trading_allowed(current_balance=4940.0, current_equity=4940.0)
        self.assertFalse(allowed)
        self.assertIn("Daily Circuit Breaker", reason)
        self.assertTrue(self.guardian.daily_halt_active)

    def test_overnight_holding_floating_loss_anchor(self):
        # Overnight runner in floating loss at reset: balance=$5,000, equity=$4,920 (-80 floating loss)
        t_reset = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
        self.guardian.reset_daily_if_new_day(t_reset, current_balance=5000.0, current_equity=4920.0)

        # Anchor must be $5,000 (higher of balance and equity)
        self.assertEqual(self.guardian.day_start_anchor, 5000.0)
        # Daily loss threshold floor is $5,000 - $200 = $4,800
        self.assertEqual(self.guardian.daily_drawdown_floor, 4800.0)

        # Since equity is currently $4,920, remaining room before breach is $4,920 - $4,800 = $120
        allowed, _ = self.guardian.is_trading_allowed(current_balance=5000.0, current_equity=4920.0)
        self.assertTrue(allowed)
        rem_risk = self.guardian.calculate_fitted_risk_usd(current_balance=5000.0, current_equity=4920.0)
        self.assertAlmostEqual(rem_risk, 50.0, delta=1.0)  # Normal 1% is $50, well within $120

        # If equity drops below $4,800 (e.g. $4,790) -> Daily CB Triggers!
        allowed, reason = self.guardian.is_trading_allowed(current_balance=4790.0, current_equity=4790.0)
        self.assertFalse(allowed)
        self.assertIn("Daily Circuit Breaker", reason)


if __name__ == '__main__':
    unittest.main()
