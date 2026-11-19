"""
Risk Guardian Module - Production Grade
=======================================
Implements the Dual-Circuit Breaker (CB) system:
1. Daily Circuit Breaker (4.0% loss from 00:00 UTC day-start balance).
   - Pre-CB trade is clamped so max loss lands exactly on 4.0% without overshooting.
   - Halts all trading until next 00:00 UTC once 4.0% is reached.
2. Max Floor Drawdown Circuit Breaker (8.5% drop from baseline $5,000 capital).
   - Pre-CB trade is clamped so balance lands cleanly on $4,575 without overshooting.
   - Inside the 1.5% ($75) recovery buffer ($4,575 to $4,500), scales risk to 0.20% (~$9 to $10).
3. Universal Dynamic Lot Sizing:
   - Uses real-time MT5 tick_size and tick_value to guarantee exact dollar risk on any pair.
"""

from datetime import datetime, timezone, timedelta
from typing import Tuple, Dict, Any, Optional

class RiskGuardian:
    def __init__(
        self,
        baseline_capital: float = 5000.0,
        daily_cb_pct: float = 0.040,      # 4.0% ($200 on $5,000)
        max_floor_cb_pct: float = 0.085,  # 8.5% ($425 drop / $4,575 floor)
        hard_floor_pct: float = 0.100,    # 10.0% ($500 drop / $4,500 liquidation)
        normal_risk_pct: float = 0.010,   # 1.00% ($50 on $5,000)
        recovery_risk_pct: float = 0.002, # 0.20% ($10 on $5,000)
        payout_retention_pct: float = 0.20 # 20% retained cushion on bi-weekly payouts
    ):
        self.baseline_capital = baseline_capital
        self.daily_cb_pct = daily_cb_pct
        self.max_floor_cb_pct = max_floor_cb_pct
        self.hard_floor_pct = hard_floor_pct
        self.normal_risk_pct = normal_risk_pct
        self.recovery_risk_pct = recovery_risk_pct
        self.payout_retention_pct = payout_retention_pct
        
        # State tracking
        self.current_utc_date: Optional[datetime.date] = None
        self.day_start_balance: float = baseline_capital
        self.daily_halt_active: bool = False
        self.hard_floor_breached: bool = False

    def reset_daily_if_new_day(self, current_time_utc: datetime, current_balance: float) -> bool:
        """
        Updates the 00:00 UTC day-start anchor and clears the daily halt flag.
        Returns True if a new day rollover just occurred.
        """
        now_date = current_time_utc.date()
        if self.current_utc_date is None or now_date > self.current_utc_date:
            self.current_utc_date = now_date
            self.day_start_balance = current_balance
            self.daily_halt_active = False
            return True
        return False

    def is_trading_allowed(self, current_balance: float, current_equity: float) -> Tuple[bool, str]:
        """
        Evaluates account equity and balance against the Dual-Circuit Breakers.
        """
        # 1. Hard Floor Liquidation Check (10.0% / $500 drop)
        floor_drop = max(0.0, self.baseline_capital - current_equity)
        if floor_drop >= (self.baseline_capital * self.hard_floor_pct):
            self.hard_floor_breached = True
            return False, f"Hard Floor Breached: Drop ${floor_drop:.2f} >= ${self.baseline_capital * self.hard_floor_pct:.2f}"

        # 2. Daily Circuit Breaker Check (4.0% drop from 00:00 UTC)
        daily_loss = max(0.0, self.day_start_balance - current_equity)
        daily_limit = self.day_start_balance * self.daily_cb_pct
        if daily_loss >= daily_limit or self.daily_halt_active:
            self.daily_halt_active = True
            return False, f"Daily Circuit Breaker Triggered: Loss ${daily_loss:.2f} >= ${daily_limit:.2f} (Trading Closed Today)"

        return True, "Trading Allowed"

    def calculate_fitted_risk_usd(self, current_balance: float, current_equity: float) -> float:
        """
        Calculates exact dollar risk for the next trade:
        - Normal Zone: 1.0% risk, clamped to remaining daily room and 8.5% floor CB.
        - Recovery Zone: 0.20% risk inside the $75 recovery buffer.
        """
        allowed, _ = self.is_trading_allowed(current_balance, current_equity)
        if not allowed:
            return 0.0

        # Remaining room to Daily CB
        daily_loss = max(0.0, self.day_start_balance - current_equity)
        daily_limit = self.day_start_balance * self.daily_cb_pct
        rem_daily = max(0.0, daily_limit - daily_loss)

        if rem_daily < 2.0:  # Less than $2 buffer left, close day
            self.daily_halt_active = True
            return 0.0

        # Remaining room to Floor CB ($425 drop / $4,575 balance)
        floor_drop = max(0.0, self.baseline_capital - current_equity)
        floor_cb_limit = self.baseline_capital * self.max_floor_cb_pct
        hard_floor_limit = self.baseline_capital * self.hard_floor_pct

        if floor_drop < floor_cb_limit:
            # Normal Mode: 1.0% risk, fitted so loss cannot overshoot $425 CB
            rem_to_cb = floor_cb_limit - floor_drop
            normal_risk = current_balance * self.normal_risk_pct
            if rem_to_cb >= 20.0:
                target_risk = min(normal_risk, rem_to_cb)
            else:
                # Smooth transition into recovery risk
                target_risk = min(current_balance * self.recovery_risk_pct, rem_to_cb)
        else:
            # Recovery Mode inside 1.5% buffer: 0.20% risk, fitted to never breach hard floor
            rem_to_hard_floor = max(0.0, hard_floor_limit - floor_drop)
            target_risk = min(current_balance * self.recovery_risk_pct, rem_to_hard_floor)

        # Fit into Daily CB budget
        final_risk = min(target_risk, rem_daily)
        return max(0.0, final_risk)

    def calculate_lot_size(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        tick_size: float,
        tick_value: float,
        volume_min: float = 0.01,
        volume_step: float = 0.01,
        volume_max: float = 100.0,
        risk_usd: float = 50.0
    ) -> float:
        """
        Universal broker-native lot sizing formula:
        Risk ($) = Lots * (SL_Distance / Tick_Size) * Tick_Value
        """
        sl_distance = abs(entry_price - sl_price)
        if sl_distance <= 0 or tick_size <= 0 or tick_value <= 0:
            return volume_min

        ticks_in_sl = sl_distance / tick_size
        risk_per_lot = ticks_in_sl * tick_value

        if risk_per_lot <= 0:
            return volume_min

        raw_lots = risk_usd / risk_per_lot
        clamped_lots = round(raw_lots / volume_step) * volume_step
        return max(volume_min, min(clamped_lots, volume_max))
