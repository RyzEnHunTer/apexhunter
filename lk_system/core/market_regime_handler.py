"""
Institutional Market Regime Handler & Dynamic Strategy Selector
===============================================================
Location: core/market_regime_handler.py

Unified Artificial Trading Brain coordinating:
1. Lewis Kelly SMC Forex Basket (EURUSD, GBPUSD, USDJPY, AUDUSD, EURJPY, USDCAD)
2. DCC ApexHunter v1.2.1 (XAUUSD, NAS100)

Core Responsibilities:
- Real-time mathematical chop & regime detection (Zero calendar curve-fitting)
- Strategy-specific failure condition guards
- News-Contingent Soft Cap & Intraday Recovery Architecture:
    * Normal Conditions: Full freedom to trade & recover intraday losses up to 4.0% ($200).
    * High-Impact News Window: Strict Soft Cap at 3.0% ($150) and pre-news entry freeze.
    * Emergency Daily CB: 4.5% ($225) automated kill-switch.
    * Prop Firm Hard Daily DD: 5.0% ($250) mathematically insulated.
    * Emergency Max CB: 8.0% ($4,600 floor on $5,000 base).
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Tuple, Optional, List

class RegimeState:
    PRIME_EXPANSION = "PRIME_EXPANSION" # Strong directional liquidity flow
    HEALTHY_TREND = "HEALTHY_TREND"     # Sustained trend with breathing room
    COMPRESSION = "COMPRESSION"         # Tight range / low ATR coiling
    DEAD_CHOP = "DEAD_CHOP"             # High range, zero net displacement (whipsaw)


class ChopDetector:
    """
    Mathematical regime and chop detection calculated dynamically on rolling historical bars.
    Never relies on calendar dates.
    """

    @staticmethod
    def calculate_choppiness_index(df_bars: pd.DataFrame, n: int = 14) -> pd.Series:
        high = df_bars['high']
        low = df_bars['low']
        close = df_bars['close']
        
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        sum_tr = tr.rolling(n).sum()
        max_hi = high.rolling(n).max()
        min_lo = low.rolling(n).min()
        
        ci = 100 * np.log10(sum_tr / (max_hi - min_lo + 1e-9)) / np.log10(n)
        return ci

    @staticmethod
    def calculate_volatility_ratio(df_bars: pd.DataFrame, short_window: int = 5, long_window: int = 20) -> pd.Series:
        high = df_bars['high']
        low = df_bars['low']
        close = df_bars['close']
        
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr_short = tr.rolling(short_window).mean()
        atr_long = tr.rolling(long_window).mean()
        
        return atr_short / (atr_long + 1e-9)

    @staticmethod
    def calculate_stretch_ratio(close: float, ema20_1h: float, atr_1h: float) -> float:
        if atr_1h <= 0:
            return 0.0
        return abs(close - ema20_1h) / atr_1h


class StrategyFailureGuard:
    """
    Enforces the quantitative failure signatures of each strategy.
    """

    @staticmethod
    def check_apexhunter_gate(
        stretch_ratio: float,
        daily_ci: float,
        vol_ratio: float,
        adx_1h: float,
        utc_hour: int
    ) -> Tuple[bool, str]:
        # Stretch ratio check (ApexHunter's #1 quality discriminator)
        if stretch_ratio < 0.90:
            return False, f"APEX_CHOP_TRAP (Stretch {stretch_ratio:.2f}x < 0.90x ATR)"

        # ADX momentum check
        if adx_1h < 18.0:
            return False, f"DEAD_ADX_MOMENTUM (1H ADX {adx_1h:.1f} < 18.0)"

        # Extreme daily chop combined with compressed volatility
        if daily_ci > 58.0 and vol_ratio < 0.88:
            return False, f"MACRO_EXTREME_CHOP (CI {daily_ci:.1f} > 58, VolRatio {vol_ratio:.2f} < 0.88)"

        return True, "APEX_PRIME_EXPANSION"

    @staticmethod
    def check_lewis_kelly_gate(
        risk_pips: float,
        asian_range_pips: float,
        adr_5_pips: float,
        is_red_folder_news: bool,
        utc_hour: int,
        utc_weekday: int
    ) -> Tuple[bool, str]:
        # Active SMC Session Window: London (07:00-11:00 UTC) and NY (12:00-17:00 UTC)
        # Asian Session (00:00-06:00 UTC) is strictly reserved for establishing dealing range
        if utc_hour < 7 or utc_hour >= 17:
            return False, f"OUTSIDE_ACTIVE_SMC_HOURS (UTC {utc_hour:02d}:00 not in 07:00-17:00)"

        # SL Outlier Check: SMC precision requires <= 15.0 pips
        if risk_pips > 15.0:
            return False, f"SL_OUTLIER_BLOWOUT (Risk {risk_pips:.1f} pips > 15.0 pips)"

        # Friday late cutoff
        if utc_weekday == 4 and utc_hour >= 16:
            return False, "FRIDAY_LATE_LIQUIDITY_DRAIN (Post 16:00 UTC)"

        # US Red folder high-impact news blackout
        if is_red_folder_news:
            return False, "RED_FOLDER_NEWS_BLACKOUT"

        # Asian Range Volatility Check
        if adr_5_pips > 0:
            asian_ratio = asian_range_pips / adr_5_pips
            if asian_ratio > 0.65:
                return False, f"ASIAN_RANGE_EXHAUSTED (Asian {asian_ratio:.2f}x > 0.65x ADR_5)"
            if asian_ratio < 0.25:
                return False, f"ASIAN_RANGE_DEAD (Asian {asian_ratio:.2f}x < 0.25x ADR_5)"

        return True, "LK_PRIME_SWEEP_ALIGNMENT"


class CircuitBreakerGuardian:
    """
    Circuit Breaker Guardian with News-Contingent Soft Capping & Intraday Recovery.
    
    Philosophy:
    - Normal Market Days (No high-impact news): Spreads are tight, wicks are controlled, slippage is negligible.
      The bot is allowed to trade up to $200 (4.0%) daily loss, enabling high-expectancy recovery trades!
    - High-Impact News Window (15m before & during news): Big candle risk, spread blowout, slippage.
      Soft Cap triggers at 3.0% ($150) to ensure unexpected news wicks cannot touch the 4.5% CB or 5.0% prop firm limit.
    - Emergency Daily CB: 4.5% ($225) hard automated kill-switch.
    """

    def __init__(
        self,
        base_capital: float = 5000.0,
        emergency_daily_cb_pct: float = 0.045, # 4.5% ($225)
        emergency_max_cb_pct: float = 0.080,   # 8.0% ($4,600 floor)
        news_soft_cap_pct: float = 0.030,      # 3.0% ($150) soft cap specifically during/before news
        normal_daily_cap_pct: float = 0.040,   # 4.0% ($200) max loss before trade entry halted on normal days
        risk_per_trade_pct: float = 0.010,     # 1.0% ($50 flat)
        max_concurrent_trades: int = 3
    ):
        self.base_capital = base_capital
        self.risk_per_trade = base_capital * risk_per_trade_pct # $50
        
        self.prop_daily_limit = base_capital * 0.05 # $250
        self.prop_max_floor = base_capital * (1.0 - 0.10) # $4,500
        
        self.emergency_daily_cb = base_capital * emergency_daily_cb_pct # $225
        self.emergency_max_floor = base_capital * (1.0 - emergency_max_cb_pct) # $4,600
        
        self.news_soft_cap = base_capital * news_soft_cap_pct # $150
        self.normal_daily_cap = base_capital * normal_daily_cap_pct # $200
        self.max_concurrent_trades = max_concurrent_trades
        
        self.current_date = None
        self.day_start_balance = base_capital
        self.realized_daily_loss = 0.0
        self.active_open_positions = 0
        self.emergency_halt_active = False

    def reset_daily_if_new_utc_day(self, current_time: datetime, current_balance: float):
        current_t_date = current_time.date()
        if self.current_date is None or current_t_date != self.current_date:
            self.current_date = current_t_date
            self.day_start_balance = current_balance
            self.realized_daily_loss = 0.0
            self.emergency_halt_active = False

    def can_open_new_trade(
        self,
        current_balance: float,
        current_equity: float,
        is_high_impact_news_window: bool = False
    ) -> Tuple[bool, str]:
        # 1. Check Max Circuit Breaker Floor ($4,600)
        if current_equity <= self.emergency_max_floor or current_balance <= self.emergency_max_floor:
            return False, f"MAX_CB_BREACH (Equity ${current_equity:.2f} <= ${self.emergency_max_floor:.2f})"

        # 2. Check Emergency Daily Circuit Breaker ($225 / 4.5%)
        current_daily_loss = self.day_start_balance - current_equity
        if current_daily_loss >= self.emergency_daily_cb:
            self.emergency_halt_active = True
            return False, f"DAILY_CB_TRIGGERED (Daily Loss ${current_daily_loss:.2f} >= ${self.emergency_daily_cb:.2f})"

        # 3. News-Contingent Soft Cap Logic:
        effective_daily_loss = max(self.realized_daily_loss, current_daily_loss)
        if is_high_impact_news_window:
            # During/before news, risk of big candles & slippage: enforce strict $150 (3.0%) cap
            if effective_daily_loss >= self.news_soft_cap:
                return False, f"NEWS_CONTINGENT_SOFT_CAP_ACTIVE (Loss ${effective_daily_loss:.2f} >= ${self.news_soft_cap:.2f} before news)"
        else:
            # Outside news, allow normal intraday recovery up to $200 (4.0%)
            # If already lost $200, taking another $50 trade would risk reaching $250 (5% prop limit)
            if effective_daily_loss >= self.normal_daily_cap:
                return False, f"NORMAL_DAILY_SOFT_CAP_REACHED (Loss ${effective_daily_loss:.2f} >= ${self.normal_daily_cap:.2f})"

        # 4. Check Concurrent Position Limits (Max 3 slots = $150 max floating open risk)
        if self.active_open_positions >= self.max_concurrent_trades:
            return False, f"MAX_CONCURRENT_POSITIONS ({self.active_open_positions} >= {self.max_concurrent_trades})"

        # 5. Risk Buffer: Ensure new trade cannot mathematically breach 5.0%
        projected_worst_loss = self.realized_daily_loss + ((self.active_open_positions + 1) * self.risk_per_trade)
        if projected_worst_loss > self.prop_daily_limit:
            return False, f"RISK_BUFFER_GUARD (Projected Loss ${projected_worst_loss:.2f} > Prop Limit ${self.prop_daily_limit:.2f})"

        return True, "RISK_APPROVED"

    def record_trade_opened(self):
        self.active_open_positions += 1

    def record_trade_closed(self, pnl: float):
        if self.active_open_positions > 0:
            self.active_open_positions -= 1
        if pnl < 0:
            self.realized_daily_loss += abs(pnl)


class DynamicStrategyMarketSelector:
    """
    Coordinates simultaneous execution of LK Forex SMC and ApexHunter Gold/Nasdaq across London and NY.
    """

    def __init__(self, guardian: CircuitBreakerGuardian):
        self.guardian = guardian

    def evaluate_market_and_route(
        self,
        strategy_candidate: str,
        symbol: str,
        current_time: datetime,
        current_balance: float,
        current_equity: float,
        candidate_params: Dict[str, Any]
    ) -> Tuple[bool, str, float]:
        self.guardian.reset_daily_if_new_utc_day(current_time, current_balance)

        is_news_window = candidate_params.get('is_high_impact_news_window', False)

        # Risk gate check
        can_trade, risk_msg = self.guardian.can_open_new_trade(
            current_balance,
            current_equity,
            is_high_impact_news_window=is_news_window
        )
        if not can_trade:
            return False, f"CIRCUIT_BREAKER_HALT: {risk_msg}", 0.0

        utc_hour = current_time.hour
        utc_weekday = current_time.weekday()

        if strategy_candidate in ['APEXHUNTER', 'APEXHUNTER_1_2_1']:
            stretch = candidate_params.get('stretch_ratio', 0.0)
            ci = candidate_params.get('daily_ci', 45.0)
            vol_ratio = candidate_params.get('vol_ratio', 1.0)
            adx = candidate_params.get('adx_1h', 25.0)

            cleared, reason = StrategyFailureGuard.check_apexhunter_gate(
                stretch_ratio=stretch,
                daily_ci=ci,
                vol_ratio=vol_ratio,
                adx_1h=adx,
                utc_hour=utc_hour
            )
            if not cleared:
                return False, f"APEX_REGIME_BLOCKED: {reason}", 0.0

        elif strategy_candidate in ['LK_FOREX', 'LK_FOREX_SMC']:
            risk_pips = candidate_params.get('risk_pips', 10.0)
            asian_range = candidate_params.get('asian_range_pips', 20.0)
            adr_5 = candidate_params.get('adr_5_pips', 60.0)
            is_red_news = candidate_params.get('is_red_folder_news', False)

            cleared, reason = StrategyFailureGuard.check_lewis_kelly_gate(
                risk_pips=risk_pips,
                asian_range_pips=asian_range,
                adr_5_pips=adr_5,
                is_red_folder_news=is_red_news,
                utc_hour=utc_hour,
                utc_weekday=utc_weekday
            )
            if not cleared:
                return False, f"LK_REGIME_BLOCKED: {reason}", 0.0

        else:
            return False, f"UNKNOWN_STRATEGY: {strategy_candidate}", 0.0

        return True, "ORDER_AUTHORIZED", self.guardian.risk_per_trade
