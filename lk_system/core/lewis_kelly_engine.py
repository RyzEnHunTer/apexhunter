"""
Lewis Kelly SMC Live & Backtest Engine (1-to-1 FixedRiskForexEngine Parity)
===========================================================================
Location: core/lewis_kelly_engine.py

Implements the complete multi-timeframe SMC institutional pipeline validated in
the Jan-Nov 2026 out-of-sample backtest (test_forex_basket_fixed_risk.py /
run_100pct_candle_unified_with_p2_propfirm.py):
1. H4 Macro Trend Bias (MarketStructure 3-candle swing & BOS/CHoCH).
2. M15 Session Killzones (US/Eastern DST-aware via SessionManager),
   M15 Structural Trend, M15 Dealing Range (50% Premium/Discount equilibrium),
   and 5-Day Rolling ADR Asian Range Filter.
3. Pair-Specific Execution Rules:
   - EURUSD (M1), USDJPY (M1), GBPUSD (M5), AUDUSD (M5), EURJPY (M5),
     USDCAD (M5, NY Killzone ONLY).
4. LTF Execution Confirmation (45-minute window after M15 sweep):
   - M1/M5 CHoCH or displacement Trend + FVG/OB retest level.
   - Structural sweep-extreme Stop Loss + min/max risk pip guard.
   - 3-Way Institutional Scaling Targets: 2.5R, 5.0R, 8.0R.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from core.dealing_range import DealingRangeEngine
from core.imbalance import ImbalanceDetector
from core.market_structure import MarketStructure
from core.session_manager import SessionManager

PAIRS_CONFIG: Dict[str, Dict[str, Any]] = {
    "EURUSD": {
        "tf_exec": "M1",
        "pip_size": 0.0001,
        "spread_pips": 0.8,
        "min_risk_pips": 4.0,
        "max_risk_pips": 15.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.40,
    },
    "GBPUSD": {
        "tf_exec": "M5",
        "pip_size": 0.0001,
        "spread_pips": 1.2,
        "min_risk_pips": 6.0,
        "max_risk_pips": 20.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.40,
    },
    "AUDUSD": {
        "tf_exec": "M5",
        "pip_size": 0.0001,
        "spread_pips": 1.0,
        "min_risk_pips": 4.5,
        "max_risk_pips": 16.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.40,
    },
    "USDJPY": {
        "tf_exec": "M1",
        "pip_size": 0.01,
        "spread_pips": 1.5,
        "min_risk_pips": 7.0,
        "max_risk_pips": 25.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.45,
    },
    "EURJPY": {
        "tf_exec": "M5",
        "pip_size": 0.01,
        "spread_pips": 1.6,
        "min_risk_pips": 8.0,
        "max_risk_pips": 25.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.45,
    },
    "USDCAD": {
        "tf_exec": "M5",
        "pip_size": 0.0001,
        "spread_pips": 1.2,
        "min_risk_pips": 5.0,
        "max_risk_pips": 18.0,
        "target_sessions": ["NY"],
        "adr_ratio": 0.40,
    },
}


@dataclass
class LKTradeSignal:
    symbol: str
    direction: str  # "BUY" or "SELL"
    entry_time: datetime
    entry_price: float
    sl_price: float
    tp1: float
    tp2: float
    tp3: float
    sl_distance: float
    h4_trend: str
    m15_trend: str
    active_session: str
    asian_high: float
    asian_low: float
    sweep_level: float


def normalize_ohlcv_to_ny(df: pd.DataFrame) -> pd.DataFrame:
    """Returns a non-mutated copy with TitleCase OHLCV columns indexed in US/Eastern."""
    out = df.copy()
    rename_map = {}
    for col in out.columns:
        low_c = col.lower()
        if low_c == "open":
            rename_map[col] = "Open"
        elif low_c == "high":
            rename_map[col] = "High"
        elif low_c == "low":
            rename_map[col] = "Low"
        elif low_c == "close":
            rename_map[col] = "Close"
        elif low_c in ("volume", "tick_volume"):
            rename_map[col] = "Volume"
    if rename_map:
        out = out.rename(columns=rename_map)

    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC").tz_convert("US/Eastern")
    else:
        out.index = out.index.tz_convert("US/Eastern")
    return out.sort_index()


class LewisKellySMCEngine:
    """
    Stateful per-symbol multi-timeframe SMC evaluator matching FixedRiskForexEngine.
    Tracks the 45-minute post-sweep CHoCH/FVG confirmation window across closed bars.
    """

    def __init__(self, symbol: str, config: Optional[Dict[str, Any]] = None):
        self.symbol = symbol
        self.config = dict(config or PAIRS_CONFIG.get(symbol, PAIRS_CONFIG["EURUSD"]))
        self.pip_size = float(self.config["pip_size"])
        self.min_risk = float(self.config["min_risk_pips"]) * self.pip_size
        self.max_risk = float(self.config["max_risk_pips"]) * self.pip_size
        self.target_sessions = list(self.config["target_sessions"])
        self.adr_ratio = float(self.config["adr_ratio"])
        self.tf_exec = str(self.config["tf_exec"])
        self.tf_minutes = 1 if self.tf_exec == "M1" else 5

        self.structure_h4 = MarketStructure(pullback_candles=3)
        self.structure_m15 = MarketStructure(pullback_candles=3)
        self.structure_exec = MarketStructure(pullback_candles=3)
        self.session_manager = SessionManager()
        self.imbalance_m15 = ImbalanceDetector(min_fvg_pips=self.pip_size)
        self.imbalance_exec = ImbalanceDetector(min_fvg_pips=self.pip_size)
        self.dealing_range = DealingRangeEngine()

        # LTF confirmation window state
        self.awaiting_choch: Optional[str] = None
        self.sweep_extreme_price: float = 0.0
        self.sweep_time: Optional[pd.Timestamp] = None
        self.recent_displacement_fvg: Optional[float] = None
        self.armed_sweep_key: Optional[tuple] = None
        self.expired_sweep_keys: set = set()
        self.expired_keys_day: Optional[Any] = None
        self.last_evaluated_exec_ts: Optional[pd.Timestamp] = None

    def _clear_arming_state(self):
        self.awaiting_choch = None
        self.sweep_extreme_price = 0.0
        self.sweep_time = None
        self.recent_displacement_fvg = None
        self.armed_sweep_key = None

    def reset_state(self):
        self._clear_arming_state()
        self.expired_sweep_keys.clear()
        self.expired_keys_day = None
        self.last_evaluated_exec_ts = None

    def prepare_frames(
        self,
        df_h4: pd.DataFrame,
        df_m15: pd.DataFrame,
        df_exec: pd.DataFrame,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[Any, float], float]:
        """Runs H4, M15, and LTF structural/session/imbalance/dealing-range preprocessing."""
        h4 = self.structure_h4.update(normalize_ohlcv_to_ny(df_h4))

        m15 = normalize_ohlcv_to_ny(df_m15)
        df_d1 = m15.resample("1D").agg({"High": "max", "Low": "min"}).dropna()
        df_d1["Range"] = df_d1["High"] - df_d1["Low"]
        df_d1["ADR_5"] = df_d1["Range"].rolling(5).mean().shift(1)
        adr_dict = {k.date(): float(v) for k, v in df_d1["ADR_5"].items() if not pd.isna(v)}
        default_adr = float(df_d1["Range"].mean()) if len(df_d1) > 0 else 70.0 * self.pip_size
        if np.isnan(default_adr) or default_adr <= 0:
            default_adr = 70.0 * self.pip_size

        m15 = self.session_manager.update(m15)
        m15 = self.structure_m15.update(m15)
        m15 = self.imbalance_m15.detect(m15)
        m15 = self.dealing_range.calculate_range(m15)

        exe = normalize_ohlcv_to_ny(df_exec)
        exe = self.structure_exec.update(exe)
        exe = self.imbalance_exec.detect(exe)

        return h4, m15, exe, adr_dict, default_adr

    def evaluate_bar_state(
        self,
        exec_row: pd.Series,
        exec_dt: pd.Timestamp,
        m15_bar: pd.Series,
        h4_bar: Optional[pd.Series],
        adr_dict: Dict[Any, float],
        default_adr: float,
    ) -> Tuple[Optional[LKTradeSignal], Dict[str, Any]]:
        """
        Evaluates a single closed execution bar against prepared H4 & M15 context.
        Matches FixedRiskForexEngine logic step-for-step.
        """
        h4_trend = str(h4_bar.get("Trend", "none")) if h4_bar is not None else "none"
        m15_trend = str(m15_bar.get("Trend", "none"))
        active_sess = str(m15_bar.get("Active_Session", "None"))
        in_premium = bool(m15_bar.get("In_Premium", False))
        in_discount = bool(m15_bar.get("In_Discount", False))

        asia_h = float(m15_bar.get("Asia_High", np.nan))
        asia_l = float(m15_bar.get("Asia_Low", np.nan))
        asia_range_pips = round((asia_h - asia_l) / self.pip_size, 1) if (not np.isnan(asia_h) and not np.isnan(asia_l) and asia_h > asia_l) else 0.0

        diag: Dict[str, Any] = {
            "symbol": self.symbol,
            "strategy": "LEWIS_KELLY_SMC",
            "timeframe": self.tf_exec,
            "open": float(exec_row.get("Open", 0.0)),
            "high": float(exec_row.get("High", 0.0)),
            "low": float(exec_row.get("Low", 0.0)),
            "close": float(exec_row.get("Close", 0.0)),
            "volume": float(exec_row.get("Volume", 0.0)),
            "asian_high": 0.0 if np.isnan(asia_h) else asia_h,
            "asian_low": 0.0 if np.isnan(asia_l) else asia_l,
            "asian_range_pips": asia_range_pips,
            "session_active": active_sess,
            "h4_trend": h4_trend,
            "m15_trend": m15_trend,
            "in_premium": in_premium,
            "in_discount": in_discount,
            "sweep_detected": False,
            "sweep_level": 0.0,
            "swept_pts": 0.0,
            "decision": "REJECT_SETUP",
            "reason": "SMC criteria not aligned",
        }

        cur_day = exec_dt.date()
        if self.expired_keys_day != cur_day:
            self.expired_sweep_keys.clear()
            self.expired_keys_day = cur_day

        if active_sess not in self.target_sessions:
            self._clear_arming_state()
            diag["decision"] = "REJECT_OUTSIDE_SESSION"
            diag["reason"] = f"Session '{active_sess}' not in {self.symbol} killzones {self.target_sessions}"
            return None, diag

        # Invalidate already-armed direction if H4 HTF bias flips directly against it
        if self.awaiting_choch == "Short" and h4_trend == "bullish":
            self._clear_arming_state()
        elif self.awaiting_choch == "Long" and h4_trend == "bearish":
            self._clear_arming_state()

        # 1. ADR_5 Asian Range Cap Filter
        if not np.isnan(asia_h) and not np.isnan(asia_l) and asia_h > asia_l:
            asian_range = asia_h - asia_l
            cur_adr = adr_dict.get(cur_day, default_adr)
            if np.isnan(cur_adr) or cur_adr <= 0:
                cur_adr = default_adr
            if (asian_range / cur_adr) > self.adr_ratio:
                self._clear_arming_state()
                diag["decision"] = "REJECT_ADR_FILTER"
                diag["reason"] = f"Asian range ratio {asian_range / cur_adr:.2f} > {self.adr_ratio:.2f}"
                return None, diag

        # 2. Session Liquidity Sweeps (M15)
        if active_sess == "London":
            swept_high = bool(m15_bar.get("Asia_Ever_Swept_High", False) or m15_bar.get("Sweep_High", False))
            swept_low = bool(m15_bar.get("Asia_Ever_Swept_Low", False) or m15_bar.get("Sweep_Low", False))
            sw_h_lvl = asia_h if not np.isnan(asia_h) else 0.0
            sw_l_lvl = asia_l if not np.isnan(asia_l) else 0.0
        else:  # NY
            swept_high = bool(
                m15_bar.get("London_Ever_Swept_High", False)
                or m15_bar.get("Asia_Ever_Swept_High", False)
                or m15_bar.get("Sweep_High", False)
            )
            swept_low = bool(
                m15_bar.get("London_Ever_Swept_Low", False)
                or m15_bar.get("Asia_Ever_Swept_Low", False)
                or m15_bar.get("Sweep_Low", False)
            )
            lon_h = float(m15_bar.get("London_High", np.nan))
            lon_l = float(m15_bar.get("London_Low", np.nan))
            sw_h_lvl = lon_h if not np.isnan(lon_h) else (0.0 if np.isnan(asia_h) else asia_h)
            sw_l_lvl = lon_l if not np.isnan(lon_l) else (0.0 if np.isnan(asia_l) else asia_l)

        h = float(exec_row["High"])
        l = float(exec_row["Low"])
        c = float(exec_row["Close"])

        # 3. HTF Bias + M15 Trend + Dealing Range Alignment -> Arm/Update LTF Window
        short_aligned = (h4_trend != "bullish") and (m15_trend == "bearish") and in_premium and swept_high
        long_aligned = (h4_trend != "bearish") and (m15_trend == "bullish") and in_discount and swept_low
        short_key = (cur_day, active_sess, "Short", round(sw_h_lvl, 5))
        long_key = (cur_day, active_sess, "Long", round(sw_l_lvl, 5))

        if short_aligned and short_key not in self.expired_sweep_keys:
            diag["sweep_detected"] = True
            diag["sweep_level"] = sw_h_lvl
            if self.awaiting_choch != "Short":
                self.awaiting_choch = "Short"
                self.sweep_extreme_price = h
                self.sweep_time = exec_dt
                self.recent_displacement_fvg = None
                self.armed_sweep_key = short_key
            elif h > self.sweep_extreme_price:
                self.sweep_extreme_price = h
        elif long_aligned and long_key not in self.expired_sweep_keys:
            diag["sweep_detected"] = True
            diag["sweep_level"] = sw_l_lvl
            if self.awaiting_choch != "Long":
                self.awaiting_choch = "Long"
                self.sweep_extreme_price = l
                self.sweep_time = exec_dt
                self.recent_displacement_fvg = None
                self.armed_sweep_key = long_key
            elif l < self.sweep_extreme_price:
                self.sweep_extreme_price = l

        if self.awaiting_choch is None:
            reasons = []
            if h4_trend == "bullish" and swept_high:
                reasons.append("H4 Bullish blocks Short")
            if h4_trend == "bearish" and swept_low:
                reasons.append("H4 Bearish blocks Long")
            if m15_trend == "none":
                reasons.append("M15 Trend neutral")
            elif not (short_aligned or long_aligned):
                reasons.append(f"M15={m15_trend}, H4={h4_trend}, Prem={in_premium}, Disc={in_discount}, SwH={swept_high}, SwL={swept_low}")
            diag["decision"] = "REJECT_HTF_SMC_FILTER"
            diag["reason"] = "; ".join(reasons) if reasons else "Awaiting HTF/M15 SMC alignment"
            return None, diag

        # 4. Check 45-Minute CHoCH / Displacement FVG Confirmation Window
        if self.sweep_time is not None and (exec_dt - self.sweep_time).total_seconds() > 2700:
            if self.armed_sweep_key is not None:
                self.expired_sweep_keys.add(self.armed_sweep_key)
            self._clear_arming_state()
            diag["decision"] = "REJECT_CHOCH_TIMEOUT"
            diag["reason"] = "45m LTF CHoCH confirmation window expired"
            return None, diag

        is_short = (self.awaiting_choch == "Short")
        return self._confirm_ltf_signal(
            is_short=is_short,
            exec_row=exec_row,
            exec_dt=exec_dt,
            h4_trend=h4_trend,
            m15_trend=m15_trend,
            active_sess=active_sess,
            sweep_level=sw_h_lvl if is_short else sw_l_lvl,
            diag=diag,
        )

    def _confirm_ltf_signal(
        self,
        is_short: bool,
        exec_row: pd.Series,
        exec_dt: pd.Timestamp,
        h4_trend: str,
        m15_trend: str,
        active_sess: str,
        sweep_level: float,
        diag: Dict[str, Any],
    ) -> Tuple[Optional[LKTradeSignal], Dict[str, Any]]:
        """Shared Short/Long LTF CHoCH & FVG/OB confirmation check."""
        h, l, c = float(exec_row["High"]), float(exec_row["Low"]), float(exec_row["Close"])
        fvg_col = "Bearish_FVG" if is_short else "Bullish_FVG"
        ob_col = "Bearish_OB_Low" if is_short else "Bullish_OB_High"
        expected_trend = "bearish" if is_short else "bullish"
        direction_str = "SELL" if is_short else "BUY"

        if is_short and h > self.sweep_extreme_price:
            self.sweep_extreme_price = h
        elif not is_short and l < self.sweep_extreme_price:
            self.sweep_extreme_price = l

        if bool(exec_row.get(fvg_col, False)):
            ob_lvl = float(exec_row.get(ob_col, np.nan))
            self.recent_displacement_fvg = ob_lvl if not np.isnan(ob_lvl) else c

        exec_choch = bool(exec_row.get("CHoCH", False))
        exec_trend = str(exec_row.get("Trend", "none"))
        if not ((exec_choch and exec_trend == expected_trend) or (exec_trend == expected_trend and self.recent_displacement_fvg is not None)):
            label = "Short (M15 Bearish+Premium+Sweep)" if is_short else "Long (M15 Bullish+Discount+Sweep)"
            diag["decision"] = "AWAITING_LTF_CHOCH"
            diag["reason"] = f"Armed {label}, awaiting {self.tf_exec} {expected_trend} CHoCH/FVG"
            return None, diag

        entry_p = c
        sl_lvl = (
            max(self.sweep_extreme_price + (2.0 * self.pip_size), entry_p + self.min_risk)
            if is_short
            else min(self.sweep_extreme_price - (2.0 * self.pip_size), entry_p - self.min_risk)
        )
        risk_dist = abs(sl_lvl - entry_p)
        if self.armed_sweep_key is not None:
            self.expired_sweep_keys.add(self.armed_sweep_key)
        self._clear_arming_state()

        if risk_dist < self.min_risk or risk_dist > self.max_risk:
            diag["decision"] = "REJECT_RISK_BOUNDS"
            diag["reason"] = f"SL distance {risk_dist / self.pip_size:.1f} pips outside [{self.config['min_risk_pips']}, {self.config['max_risk_pips']}]"
            return None, diag

        sign = -1.0 if is_short else 1.0
        tp1 = entry_p + (sign * 2.50 * risk_dist)
        tp2 = entry_p + (sign * 5.00 * risk_dist)
        tp3 = entry_p + (sign * 8.00 * risk_dist)
        diag["decision"] = f"SIGNAL_{direction_str}"
        diag["reason"] = f"SMC {direction_str.title()} confirmed (H4={h4_trend}, M15={m15_trend}, LTF CHoCH/FVG)"
        sig = LKTradeSignal(
            symbol=self.symbol,
            direction=direction_str,
            entry_time=exec_dt.to_pydatetime().astimezone(timezone.utc),
            entry_price=entry_p,
            sl_price=sl_lvl,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            sl_distance=risk_dist,
            h4_trend=h4_trend,
            m15_trend=m15_trend,
            active_session=active_sess,
            asian_high=diag["asian_high"],
            asian_low=diag["asian_low"],
            sweep_level=sweep_level,
        )
        return sig, diag

    def evaluate(
        self,
        df_h4: pd.DataFrame,
        df_m15: pd.DataFrame,
        df_exec: pd.DataFrame,
    ) -> Tuple[Optional[LKTradeSignal], Dict[str, Any]]:
        """
        Live evaluation entry point using strictly closed H4, M15, and LTF bars.
        """
        if df_h4 is None or df_m15 is None or df_exec is None or len(df_h4) < 10 or len(df_m15) < 10 or len(df_exec) < 5:
            return None, {
                "symbol": self.symbol,
                "strategy": "LEWIS_KELLY_SMC",
                "timeframe": self.tf_exec,
                "decision": "REJECT_INSUFFICIENT_DATA",
                "reason": "Insufficient closed H4/M15/LTF bars",
            }

        h4_prep, m15_prep, exec_prep, adr_dict, default_adr = self.prepare_frames(df_h4, df_m15, df_exec)
        exec_dt = exec_prep.index[-1]

        if self.last_evaluated_exec_ts is not None and exec_dt <= self.last_evaluated_exec_ts:
            return None, {
                "symbol": self.symbol,
                "strategy": "LEWIS_KELLY_SMC",
                "timeframe": self.tf_exec,
                "decision": "REJECT_DUPLICATE_BAR",
                "reason": f"Bar {exec_dt} already evaluated",
            }

        def _closed_htf_bars(target_exec_dt: pd.Timestamp) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
            close_dt = target_exec_dt + pd.Timedelta(minutes=self.tf_minutes)
            m15_sub = m15_prep[m15_prep.index <= close_dt - pd.Timedelta(minutes=15)]
            h4_sub = h4_prep[h4_prep.index <= close_dt - pd.Timedelta(hours=4)]
            m15_b = m15_sub.iloc[-1] if not m15_sub.empty else None
            h4_b = h4_sub.iloc[-1] if not h4_sub.empty else None
            return m15_b, h4_b

        # Synchronize arming state across recent missed bars on cold start or post-trade gaps
        if self.last_evaluated_exec_ts is None:
            replay_start = exec_dt - pd.Timedelta(minutes=45)
            missed_bars = exec_prep[(exec_prep.index >= replay_start) & (exec_prep.index < exec_dt)]
        else:
            gap_limit = exec_dt - pd.Timedelta(minutes=max(45, self.tf_minutes * 60))
            since_ts = max(self.last_evaluated_exec_ts, gap_limit)
            missed_bars = exec_prep[(exec_prep.index > since_ts) & (exec_prep.index < exec_dt)]

        for prev_dt, prev_row in missed_bars.iterrows():
            m15_prev, h4_prev = _closed_htf_bars(prev_dt)
            if m15_prev is not None:
                self.evaluate_bar_state(prev_row, prev_dt, m15_prev, h4_prev, adr_dict, default_adr)

        m15_bar, h4_bar = _closed_htf_bars(exec_dt)
        if m15_bar is None:
            return None, {
                "symbol": self.symbol,
                "strategy": "LEWIS_KELLY_SMC",
                "timeframe": self.tf_exec,
                "decision": "REJECT_NO_M15_CONTEXT",
                "reason": "No closed M15 context available",
            }

        exec_row = exec_prep.iloc[-1]
        self.last_evaluated_exec_ts = exec_dt
        return self.evaluate_bar_state(exec_row, exec_dt, m15_bar, h4_bar, adr_dict, default_adr)
