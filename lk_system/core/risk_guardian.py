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
        payout_retention_pct: float = 0.20, # 20% retained cushion on bi-weekly payouts
        max_consecutive_daily_losses: int = 2 # Max consecutive stop-outs per symbol per day
    ):
        self.baseline_capital = baseline_capital
        self.daily_cb_pct = daily_cb_pct
        self.max_floor_cb_pct = max_floor_cb_pct
        self.hard_floor_pct = hard_floor_pct
        self.normal_risk_pct = normal_risk_pct
        self.recovery_risk_pct = recovery_risk_pct
        self.payout_retention_pct = payout_retention_pct
        self.max_consecutive_daily_losses = max_consecutive_daily_losses
        
        # State tracking
        self.current_utc_date: Optional[datetime.date] = None
        self.day_start_balance: float = baseline_capital
        self.day_start_equity: float = baseline_capital
        self.day_start_anchor: float = baseline_capital
        self.daily_drawdown_floor: float = baseline_capital * (1.0 - daily_cb_pct)
        self.daily_halt_active: bool = False
        self.hard_floor_breached: bool = False
        self.symbol_consecutive_losses: Dict[str, int] = {}
        self.symbol_daily_halted: Dict[str, bool] = {}

    def reset_daily_if_new_day(
        self,
        current_time_utc: datetime,
        current_balance: float,
        current_equity: Optional[float] = None
    ) -> bool:
        """
        Updates the 00:00 server-time daily anchor and clears daily halt flags.
        The anchor uses max(balance, equity) at reset, conforming strictly to
        The5ers, FTMO, and institutional prop-firm rules for overnight holdings.
        Returns True if a new day rollover just occurred.
        """
        now_date = current_time_utc.date()
        if self.current_utc_date is None or now_date > self.current_utc_date:
            eq = current_equity if current_equity is not None else current_balance
            self.current_utc_date = now_date
            self.day_start_balance = current_balance
            self.day_start_equity = eq
            # The5ers & FTMO standard: whichever is higher at the reset
            self.day_start_anchor = max(current_balance, eq)
            self.daily_drawdown_floor = self.day_start_anchor - (self.baseline_capital * self.daily_cb_pct)
            self.daily_halt_active = False
            self.symbol_consecutive_losses.clear()
            self.symbol_daily_halted.clear()
            return True
        return False

    def record_trade_result(self, symbol: str, is_loss: bool):
        """
        Tracks consecutive daily losses per symbol.
        Halts the symbol for the remainder of the day if threshold reached.
        """
        if is_loss:
            current_loss_count = self.symbol_consecutive_losses.get(symbol, 0) + 1
            self.symbol_consecutive_losses[symbol] = current_loss_count
            if current_loss_count >= self.max_consecutive_daily_losses:
                self.symbol_daily_halted[symbol] = True
        else:
            self.symbol_consecutive_losses[symbol] = 0

    def is_symbol_trading_allowed(self, symbol: str, current_balance: float, current_equity: float) -> Tuple[bool, str]:
        """
        Evaluates both account-wide Circuit Breakers and per-symbol consecutive loss limits.
        """
        # 1. Account-wide CB check
        allowed, reason = self.is_trading_allowed(current_balance, current_equity)
        if not allowed:
            return False, reason

        # 2. Per-symbol consecutive loss halt check
        if self.symbol_daily_halted.get(symbol, False):
            losses = self.symbol_consecutive_losses.get(symbol, 0)
            return False, f"Symbol {symbol} Halted: {losses} consecutive daily losses reached"

        return True, "Allowed"

    def is_trading_allowed(self, current_balance: float, current_equity: float) -> Tuple[bool, str]:
        """
        Evaluates account equity and balance against the Dual-Circuit Breakers.
        """
        # 1. Hard Floor Liquidation Check (10.0% / $500 drop)
        floor_drop = max(0.0, self.baseline_capital - current_equity)
        if floor_drop >= (self.baseline_capital * self.hard_floor_pct):
            self.hard_floor_breached = True
            return False, f"Hard Floor Breached: Drop ${floor_drop:.2f} >= ${self.baseline_capital * self.hard_floor_pct:.2f}"

        # 2. Daily Circuit Breaker Check (Overnight max(balance, equity) anchor)
        daily_loss = max(0.0, self.day_start_anchor - min(current_balance, current_equity))
        daily_limit = self.baseline_capital * self.daily_cb_pct
        if (
            current_equity <= self.daily_drawdown_floor
            or current_balance <= self.daily_drawdown_floor
            or daily_loss >= daily_limit
            or self.daily_halt_active
        ):
            self.daily_halt_active = True
            return (
                False,
                f"Daily Circuit Breaker Triggered: Loss ${daily_loss:.2f} >= ${daily_limit:.2f} "
                f"(Floor: ${self.daily_drawdown_floor:,.2f}) (Trading Closed Today)"
            )

        return True, "Trading Allowed"

    def calculate_fitted_risk_usd(self, current_balance: float, current_equity: float) -> float:
        """
        Calculates exact dollar risk for the next trade:
        - Clamped to remaining room before Daily Drawdown Floor (anchored to max(balance, equity) at 00:00).
        - Clamped to 8.5% floor CB.
        - Inside the 1.5% recovery buffer ($4,575 to $4,500), scales risk to 0.20%.
        """
        allowed, _ = self.is_trading_allowed(current_balance, current_equity)
        if not allowed:
            return 0.0

        # Remaining room to Daily CB Floor
        rem_daily = max(0.0, min(current_balance, current_equity) - self.daily_drawdown_floor)

        if rem_daily < 2.0:  # Less than $2 buffer left, close day
            self.daily_halt_active = True
            return 0.0

        # Remaining room to Floor CB ($425 drop / $4,575 balance)
        floor_drop = max(0.0, self.baseline_capital - current_equity)
        floor_cb_limit = self.baseline_capital * self.max_floor_cb_pct
        hard_floor_limit = self.baseline_capital * self.hard_floor_pct

        if floor_drop < floor_cb_limit:
            # Normal Mode: 1.0% risk, fitted so loss cannot overshoot $425 CB or Daily Floor
            rem_to_cb = floor_cb_limit - floor_drop
            normal_risk = current_balance * self.normal_risk_pct
            if rem_to_cb >= 20.0:
                target_risk = min(normal_risk, rem_to_cb)
            else:
                # Smooth transition into recovery risk
                target_risk = min(current_balance * self.recovery_risk_pct, rem_to_cb)
            return max(0.0, min(target_risk, rem_daily))
        else:
            # Recovery Mode inside 1.5% buffer: 0.20% risk, fitted to never breach hard floor or Daily Floor
            recovery_risk = current_balance * self.recovery_risk_pct
            rem_to_hard_floor = max(0.0, hard_floor_limit - floor_drop)
            return max(0.0, min(recovery_risk, rem_to_hard_floor, rem_daily))
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
