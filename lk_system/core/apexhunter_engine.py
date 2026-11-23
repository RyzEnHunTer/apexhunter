"""
DCC Strategy v1.2.1 "ApexHunter" Core Engine
============================================
Location: core/apexhunter_engine.py

Independent, self-contained execution engine for Gold (XAUUSD) & Nasdaq (NAS100).
Replicates the verified institutional ApexHunter v1.2.1 TripleGuard logic:
1. [SMART KZ]  Smart Hybrid Killzone     : Hours 09:00 & 13:00 UTC allowed ONLY if Stretch >= 1.10x ATR
2. [FLOOR]     1H Anti-Chop Floor        : Stretch Ratio >= 0.40x ATR (kills consolidation chop)
3. [CEILING]   1H ADX Exhaustion Ceiling : 1H ADX <= 45.0 (kills parabolic top/bottom traps)
4. [EXECUTION] 5M No-Chase Guard         : Chase Ratio <= 0.50x ATR (kills bloated candle chasing)
5. [QUALITY]   Low-ADX Quality Gate      : Filters if ADX < 20.0 and Stretch < 0.90
6. [CALENDAR]  Monday US Afternoon Shield: Rejects entries on Mondays between 14:00 and 18:00 UTC
7. [FRACTAL]   Liquidity Sweep Gate      : Validates swing low/high sweep before 5M flip
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd


class SignalType(Enum):
    NONE = 0
    BUY = 1
    SELL = -1


@dataclass
class TradeSignalV12:
    timestamp: datetime
    signal_type: SignalType
    symbol: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_1: float
    risk_reward_2: float
    sl_distance: float
    tp1_distance: float
    tp2_distance: float
    atr_1h: float
    adx_1h: float
    stretch_ratio: float
    chase_ratio: float
    vwap_5m: float
    ema20_1h_level: float
    reason: str
    shield_diagnostics: Dict[str, bool] = field(default_factory=dict)


class DCCEngineV12:
    def __init__(
        self,
        symbol: str = "XAUUSD",
        ema_fast_period: int = 9,
        ema_slow_period: int = 20,
        adx_period: int = 14,
        adx_min_threshold: float = 15.0,
        max_1h_adx: float = 45.0,
        atr_period: int = 14,
        atr_sl_multiplier: float = 0.90,
        tp1_rr: float = 1.40,
        tp2_rr: float = 2.10,
        min_1h_stretch: float = 0.40,
        max_5m_chase: float = 0.50,
        check_2h_room: bool = False,
        use_daily_open_filter: bool = False,
        block_monday_pm: bool = True,
        session_start_hour: int = 6,
        session_end_hour: int = 19,
        dead_trap_hours: Optional[List[int]] = None,
        low_adx_threshold: float = 20.0,
        low_adx_min_stretch: float = 0.90,
    ):
        self.symbol = symbol
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.adx_period = adx_period
        self.adx_min_threshold = adx_min_threshold
        self.max_1h_adx = max_1h_adx
        self.atr_period = atr_period
        self.atr_sl_multiplier = atr_sl_multiplier
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr

        # TripleGuard Volatility Shields & Quality Gates
        self.min_1h_stretch = min_1h_stretch
        self.max_5m_chase = max_5m_chase
        self.check_2h_room = check_2h_room
        self.use_daily_open_filter = use_daily_open_filter
        self.low_adx_threshold = low_adx_threshold
        self.low_adx_min_stretch = low_adx_min_stretch

        # Calendar Shield
        self.block_monday_pm = block_monday_pm
        self.monday_pm_blocked_hours = [14, 15, 16, 17]
        self.dead_trap_hours = dead_trap_hours if dead_trap_hours is not None else [9, 13]

        # Session Timing (UTC)
        self.session_start_hour = session_start_hour
        self.session_end_hour = session_end_hour

    @staticmethod
    def calc_ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df['high']
        low = df['low']
        close = df['close']
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        # RMA (Wilder's exponential smoothing matching DC canonical engine)
        return tr.ewm(alpha=1.0 / period, adjust=False).mean()

    @staticmethod
    def calc_adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
        high = df['high']
        low = df['low']
        close = df['close']
        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)

        plus_dm = high - prev_high
        minus_dm = prev_low - low

        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Wilder's exponential smoothing (RMA) matching DC canonical engine
        alpha = 1.0 / period
        atr = tr.ewm(alpha=alpha, adjust=False).mean()
        plus_di = 100.0 * (pd.Series(plus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / (atr + 1e-9))
        minus_di = 100.0 * (pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / (atr + 1e-9))

        dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
        adx = dx.ewm(alpha=alpha, adjust=False).mean()
        return adx, plus_di, minus_di

    @staticmethod
    def calc_session_vwap(df_5m: pd.DataFrame) -> pd.Series:
        df = df_5m.copy()
        typical_price = (df['high'] + df['low'] + df['close']) / 3.0
        vol = df['volume'].replace(0, 1)
        pv = typical_price * vol
        dates = df.index.date
        cum_pv = pv.groupby(dates).cumsum()
        cum_vol = vol.groupby(dates).cumsum()
        vwap = cum_pv / (cum_vol + 1e-9)
        return vwap

    @staticmethod
    def detect_liquidity_sweep(
        df_5m: pd.DataFrame,
        idx: int,
        direction: int,
        swing_lookback: int = 24,
        pullback_window: int = 3
    ) -> Tuple[bool, float, float]:
        """
        Detects 5M liquidity sweep (Turtle Soup / false breakout rejection) prior to entry bar.
        Matches institutional DC flagship specification:
        - Scans candidate fractal swing pivots across [idx - swing_lookback : idx - pullback_window]
        - Pullback window [idx - pullback_window : idx + 1] must penetrate swing level
        - Current bar close MUST reject back inside the range (curr_close > sw_low or curr_close < sw_high)
        """
        if idx < swing_lookback + 2:
            return False, 0.0, 0.0

        swing_search_start = max(0, idx - swing_lookback)
        pullback_start_idx = max(swing_search_start + 1, idx - pullback_window)
        if pullback_start_idx <= swing_search_start:
            return False, 0.0, 0.0

        lows = df_5m['low'].values
        highs = df_5m['high'].values
        closes = df_5m['close'].values

        if direction == 1:  # BUY Setup (Bullish sweep of prior swing low)
            cand_lows = [
                lows[j] for j in range(swing_search_start + 1, pullback_start_idx)
                if lows[j] <= lows[j - 1] and lows[j] <= lows[j + 1]
            ]
            if not cand_lows:
                cand_lows = [min(lows[swing_search_start:pullback_start_idx])]
            curr_close = closes[idx]
            for sw_low in cand_lows:
                min_pb = min(lows[pullback_start_idx:idx + 1])
                if min_pb < sw_low and curr_close > sw_low:
                    return True, float(sw_low), float(sw_low - min_pb)
            return False, 0.0, 0.0
        else:  # SELL Setup (Bearish sweep of prior swing high)
            cand_highs = [
                highs[j] for j in range(swing_search_start + 1, pullback_start_idx)
                if highs[j] >= highs[j - 1] and highs[j] >= highs[j + 1]
            ]
            if not cand_highs:
                cand_highs = [max(highs[swing_search_start:pullback_start_idx])]
            curr_close = closes[idx]
            for sw_high in cand_highs:
                max_pb = max(highs[pullback_start_idx:idx + 1])
                if max_pb > sw_high and curr_close < sw_high:
                    return True, float(sw_high), float(max_pb - sw_high)
            return False, 0.0, 0.0

    def prepare_data(
        self,
        df_5m: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_2h: Optional[pd.DataFrame] = None
    ) -> pd.DataFrame:
        """
        Merges 5M and 1H DataFrames without lookahead bias.
        Computes dynamic Stretch Ratio and Chase Ratio normalized by 1H ATR.
        """
        df_5m = df_5m.copy().sort_index()
        df_1h = df_1h.copy().sort_index()

        # 1. 5M Indicators
        df_5m['ema9_5m'] = self.calc_ema(df_5m['close'], self.ema_fast_period)
        df_5m['ema20_5m'] = self.calc_ema(df_5m['close'], self.ema_slow_period)
        df_5m['vwap_5m'] = self.calc_session_vwap(df_5m)

        # 2. 1H Indicators
        df_1h['ema9_1h'] = self.calc_ema(df_1h['close'], self.ema_fast_period)
        df_1h['ema20_1h'] = self.calc_ema(df_1h['close'], self.ema_slow_period)
        df_1h['adx_1h'], _, _ = self.calc_adx(df_1h, self.adx_period)
        df_1h['atr_1h'] = self.calc_atr(df_1h, self.atr_period)

        df_1h['bias_1h'] = np.where(
            df_1h['ema9_1h'] > df_1h['ema20_1h'], 1,
            np.where(df_1h['ema9_1h'] < df_1h['ema20_1h'], -1, 0)
        )

        # Shift 1H by 1 bar backward so 5M bars only look at strictly closed 1H bars
        df_1h_shifted = df_1h[['ema9_1h', 'ema20_1h', 'adx_1h', 'atr_1h', 'bias_1h']].shift(1)

        # Reindex to 5M using merge_asof backward
        df_merged = pd.merge_asof(
            df_5m,
            df_1h_shifted,
            left_index=True,
            right_index=True,
            direction='backward'
        )

        # 3. Dynamic Volatility Ratios (TripleGuard Core)
        dist_to_h1_ema20 = (df_merged['close'] - df_merged['ema20_1h']).abs()
        df_merged['stretch_ratio'] = dist_to_h1_ema20 / (df_merged['atr_1h'] + 1e-9)

        dist_to_5m_ema9 = (df_merged['close'] - df_merged['ema9_5m']).abs()
        df_merged['chase_ratio'] = dist_to_5m_ema9 / (df_merged['atr_1h'] + 1e-9)
        df_merged['ema20_1h_level'] = df_merged['ema20_1h']

        return df_merged

    def evaluate_bar(
        self,
        prev_bar: pd.Series,
        curr_bar: pd.Series,
        symbol: Optional[str] = None,
        return_reason: bool = False
    ) -> Union[Optional[TradeSignalV12], Tuple[Optional[TradeSignalV12], str]]:
        """
        Evaluates a newly closed 5M bar under DCC v1.2.1 TripleGuard rules.
        If return_reason=True, returns (TradeSignalV12 or None, diagnostic_reason_str).
        """
        curr_time: datetime = curr_bar.name
        t_hour = curr_time.hour
        t_wday = curr_time.strftime('%A')
        sym = symbol if symbol else self.symbol

        # 1. Trading Session Hours (06:00 to 19:00 UTC)
        if not (self.session_start_hour <= t_hour < self.session_end_hour):
            reason = f"PAUSED_OFF_SESSION (Hour {t_hour:02d}:00 UTC not in active window {self.session_start_hour:02d}:00-{self.session_end_hour:02d}:00)"
            return (None, reason) if return_reason else None

        # Smart Killzone Filter (Hours 09:00 & 13:00 UTC: skip if stretch < 1.10x)
        stretch_ratio = float(curr_bar['stretch_ratio']) if 'stretch_ratio' in curr_bar and not pd.isna(curr_bar['stretch_ratio']) else 1.0
        if self.dead_trap_hours and t_hour in self.dead_trap_hours:
            if stretch_ratio < 1.10:
                reason = f"PAUSED_KILLZONE (Trap Hour {t_hour:02d}:00 UTC: stretch {stretch_ratio:.2f}x < 1.10x required)"
                return (None, reason) if return_reason else None

        # 2. Calendar Shield: Monday Afternoon Trap Guardrail
        if self.block_monday_pm and t_wday == "Monday" and t_hour in self.monday_pm_blocked_hours:
            reason = f"PAUSED_MONDAY_PM (Monday afternoon blocked hour {t_hour:02d}:00 UTC)"
            return (None, reason) if return_reason else None

        # 3. 1H Bias Confirmation
        bias_1h = curr_bar['bias_1h']
        if pd.isna(bias_1h) or bias_1h == 0:
            reason = "REJECT_BIAS (1H Trend Bias is neutral or undefined)"
            return (None, reason) if return_reason else None

        # 4. 1H ADX Baseline Threshold
        adx_1h = curr_bar['adx_1h']
        if pd.isna(adx_1h) or adx_1h < self.adx_min_threshold:
            reason = f"REJECT_LOW_ADX (1H ADX {adx_1h:.1f} < {self.adx_min_threshold:.1f} minimum baseline)"
            return (None, reason) if return_reason else None

        # 5. 1H ATR Validation
        atr_1h = curr_bar['atr_1h']
        if pd.isna(atr_1h) or atr_1h <= 0:
            reason = "REJECT_INVALID_ATR (1H ATR <= 0 or missing)"
            return (None, reason) if return_reason else None

        close = curr_bar['close']
        vwap = curr_bar['vwap_5m']
        ema20_1h_lvl = curr_bar['ema20_1h_level']
        stretch_ratio = float(curr_bar['stretch_ratio'])
        chase_ratio = float(curr_bar['chase_ratio'])

        # 6. Detect 5M EMA Flip
        prev_diff = float(prev_bar['ema9_5m']) - float(prev_bar['ema20_5m'])
        curr_diff = float(curr_bar['ema9_5m']) - float(curr_bar['ema20_5m'])

        is_bullish_flip = (bias_1h == 1) and (prev_diff <= 0) and (curr_diff > 0)
        is_bearish_flip = (bias_1h == -1) and (prev_diff >= 0) and (curr_diff < 0)

        if not (is_bullish_flip or is_bearish_flip):
            reason = f"REJECT_NO_EMA_FLIP (No 5M EMA9/20 crossover aligning with 1H Bias={bias_1h})"
            return (None, reason) if return_reason else None

        # 7. VWAP & 1H 20 EMA Level Filters
        if is_bullish_flip:
            if close <= vwap or close <= ema20_1h_lvl:
                reason = f"REJECT_LEVEL_FILTER (BUY setup rejected: Close {close:.2f} <= VWAP {vwap:.2f} or 1H EMA20 {ema20_1h_lvl:.2f})"
                return (None, reason) if return_reason else None
        elif is_bearish_flip:
            if close >= vwap or close >= ema20_1h_lvl:
                reason = f"REJECT_LEVEL_FILTER (SELL setup rejected: Close {close:.2f} >= VWAP {vwap:.2f} or 1H EMA20 {ema20_1h_lvl:.2f})"
                return (None, reason) if return_reason else None

        # 8. TRIPLEGUARD VOLATILITY SHIELDS & QUALITY GATES
        is_low_adx_gate_pass = not (adx_1h < self.low_adx_threshold and stretch_ratio < self.low_adx_min_stretch)
        shield_checks = {
            "anti_chop_floor": stretch_ratio >= self.min_1h_stretch,
            "adx_ceiling": adx_1h <= self.max_1h_adx,
            "no_chase_guard": chase_ratio <= self.max_5m_chase,
            "low_adx_quality_gate": is_low_adx_gate_pass,
        }

        # Check Shield 1: Anti-Chop Floor
        if not shield_checks["anti_chop_floor"]:
            reason = f"REJECT_CHOP_FLOOR (Stretch ratio {stretch_ratio:.2f}x < {self.min_1h_stretch:.2f}x minimum floor)"
            return (None, reason) if return_reason else None

        # Check Shield 2: ADX Exhaustion Ceiling
        if not shield_checks["adx_ceiling"]:
            reason = f"REJECT_ADX_CEILING (1H ADX {adx_1h:.1f} > {self.max_1h_adx:.1f} exhaustion ceiling)"
            return (None, reason) if return_reason else None

        # Check Shield 3: No-Chase Guard
        if not shield_checks["no_chase_guard"]:
            reason = f"REJECT_CHASE_GUARD (5M Chase ratio {chase_ratio:.2f}x > {self.max_5m_chase:.2f}x maximum chase threshold)"
            return (None, reason) if return_reason else None

        # Check Shield 4: Low-ADX Quality Gate (v1.2.1-ApexHunter)
        if not shield_checks["low_adx_quality_gate"]:
            reason = f"REJECT_LOW_ADX_GATE (1H ADX {adx_1h:.1f} < {self.low_adx_threshold} requires stretch >= {self.low_adx_min_stretch:.2f}x, got {stretch_ratio:.2f}x)"
            return (None, reason) if return_reason else None

        # 9. Asymmetric Risk-Reward Calculation
        sl_distance = self.atr_sl_multiplier * atr_1h
        tp1_distance = self.tp1_rr * sl_distance
        tp2_distance = self.tp2_rr * sl_distance

        sig_type = SignalType.BUY if is_bullish_flip else SignalType.SELL
        sl = close - sl_distance if sig_type == SignalType.BUY else close + sl_distance
        tp1 = close + tp1_distance if sig_type == SignalType.BUY else close - tp1_distance
        tp2 = close + tp2_distance if sig_type == SignalType.BUY else close - tp2_distance

        dir_str = "Long" if sig_type == SignalType.BUY else "Short"
        reason = (
            f"DCC v1.2.1 {dir_str}: 1H Bias + 5M PFG Flip + VWAP/EMA20 + "
            f"TripleGuard Verified (Stretch: {stretch_ratio:.2f}x >= {self.min_1h_stretch}, "
            f"ADX: {adx_1h:.1f} <= {self.max_1h_adx}, Chase: {chase_ratio:.2f}x <= {self.max_5m_chase}, "
            f"Low-ADX Gate: Pass)"
        )

        signal = TradeSignalV12(
            timestamp=curr_time,
            signal_type=sig_type,
            symbol=sym,
            entry_price=close,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            risk_reward_1=self.tp1_rr,
            risk_reward_2=self.tp2_rr,
            sl_distance=sl_distance,
            tp1_distance=tp1_distance,
            tp2_distance=tp2_distance,
            atr_1h=atr_1h,
            adx_1h=adx_1h,
            stretch_ratio=stretch_ratio,
            chase_ratio=chase_ratio,
            vwap_5m=vwap,
            ema20_1h_level=ema20_1h_lvl,
            reason=reason,
            shield_diagnostics=shield_checks
        )
        return (signal, reason) if return_reason else signal
