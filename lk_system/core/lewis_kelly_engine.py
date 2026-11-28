"""
Lewis Kelly SMC Live & Backtest Engine (Causal Zero-Lookahead C1 Architecture)
==============================================================================
Location: core/lewis_kelly_engine.py

Implements the causal multi-timeframe SMC institutional pipeline validated across
Jan-Nov 2026 walk-forward simulation and live MT5 parity tests:
1. H4 Macro Trend Bias (causal MarketStructure 3-candle swing & BOS/CHoCH).
2. M5 Session Killzones (US/Eastern DST-aware via SessionManager),
   M5 Structural Trend, M5 Dealing Range (50% Premium/Discount equilibrium),
   and causal NY-17:00 FX-day ADR_5 Asian Range Filter (computed from M15/M5).
3. Pair-Specific Execution Rules (all M5 execution, 1.5x ADR thresholds):
   - EURUSD, GBPUSD, AUDUSD, USDJPY, EURJPY (London & NY), USDCAD (NY only).
4. M5 Confirmation (45-minute window after session liquidity sweep):
   - M5 CHoCH or displacement Trend + FVG/OB retest level.
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
        "tf_exec": "M5",
        "pip_size": 0.0001,
        "spread_pips": 0.8,
        "min_risk_pips": 4.0,
        "max_risk_pips": 15.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.60,
    },
    "GBPUSD": {
        "tf_exec": "M5",
        "pip_size": 0.0001,
        "spread_pips": 1.2,
        "min_risk_pips": 6.0,
        "max_risk_pips": 20.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.60,
    },
    "AUDUSD": {
        "tf_exec": "M5",
        "pip_size": 0.0001,
        "spread_pips": 1.0,
        "min_risk_pips": 4.5,
        "max_risk_pips": 16.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.60,
    },
    "USDJPY": {
        "tf_exec": "M5",
        "pip_size": 0.01,
        "spread_pips": 1.5,
        "min_risk_pips": 7.0,
        "max_risk_pips": 25.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.675,
    },
    "EURJPY": {
        "tf_exec": "M5",
        "pip_size": 0.01,
        "spread_pips": 1.6,
        "min_risk_pips": 8.0,
        "max_risk_pips": 25.0,
        "target_sessions": ["London", "NY"],
        "adr_ratio": 0.675,
    },
    "USDCAD": {
        "tf_exec": "M5",
        "pip_size": 0.0001,
        "spread_pips": 1.2,
        "min_risk_pips": 5.0,
        "max_risk_pips": 18.0,
        "target_sessions": ["NY"],
        "adr_ratio": 0.60,
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
        self.structure_exec = MarketStructure(pullback_candles=3)
        self.session_manager = SessionManager()
        self.imbalance_exec = ImbalanceDetector(min_fvg_pips=self.pip_size)
        self.dealing_range = DealingRangeEngine()

        # LTF confirmation window & per-session sweep extreme state
        self.awaiting_choch: Optional[str] = None
        self.sweep_extreme_price: float = 0.0
        self.sweep_time: Optional[pd.Timestamp] = None
        self.recent_displacement_fvg: Optional[float] = None
        self.last_evaluated_exec_ts: Optional[pd.Timestamp] = None
        self._session_key: Optional[tuple] = None
        self._session_high: float = np.nan
        self._session_low: float = np.nan

    def _clear_arming_state(self):
        self.awaiting_choch = None
        self.sweep_extreme_price = 0.0
        self.sweep_time = None
        self.recent_displacement_fvg = None

    def reset_state(self):
        self._clear_arming_state()
        self.last_evaluated_exec_ts = None
        self._session_key = None
        self._session_high = np.nan
        self._session_low = np.nan

    def prepare_frames(
        self,
        df_h4: pd.DataFrame,
        df_m15: Optional[pd.DataFrame],
        df_exec: pd.DataFrame,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[Any, float], float]:
        """Runs causal H4 bias + M5 session/structure/dealing-range/imbalance preprocessing."""
        h4 = self.structure_h4.update(normalize_ohlcv_to_ny(df_h4))

        exe = normalize_ohlcv_to_ny(df_exec)
        adr_src = normalize_ohlcv_to_ny(df_m15) if (df_m15 is not None and len(df_m15) > 0) else exe

        # Group by NY 17:00 rollover day so Sunday 17:00-23:59 EST merges cleanly into Monday
        static_fallback_adr = 70.0 * self.pip_size
        adr_dict: Dict[Any, float] = {}
        if len(adr_src) > 0:
            fx_dates = pd.Series((adr_src.index + pd.Timedelta(hours=7)).date, index=adr_src.index)
            df_d1 = pd.DataFrame({"High": adr_src["High"], "Low": adr_src["Low"], "fx_day": fx_dates})
            df_d1 = df_d1.groupby("fx_day", sort=True).agg({"High": "max", "Low": "min"}).dropna()
            df_d1["Range"] = df_d1["High"] - df_d1["Low"]
            # Strictly causal shifted expanding + 5-day rolling ADR (never reads current/future days)
            df_d1["ADR_exp"] = df_d1["Range"].expanding().mean().shift(1)
            df_d1["ADR_5"] = df_d1["Range"].rolling(5).mean().shift(1).fillna(df_d1["ADR_exp"])
            adr_dict = {k: float(v) for k, v in df_d1["ADR_5"].items() if pd.notna(v) and v > 0}

        exe = self.session_manager.update(exe)
        exe = self.structure_exec.update(exe)
        exe = self.imbalance_exec.detect(exe)
        exe = self.dealing_range.calculate_range(exe)

        return h4, adr_src, exe, adr_dict, static_fallback_adr

    def evaluate_bar_state(
        self,
        exec_row: pd.Series,
        exec_dt: pd.Timestamp,
        m15_bar: Optional[pd.Series],
        h4_bar: Optional[pd.Series],
        adr_dict: Dict[Any, float],
        default_adr: float,
    ) -> Tuple[Optional[LKTradeSignal], Dict[str, Any]]:
        """
        Evaluates a single closed M5 bar using closed H4 bias and real-time closed M5
        SessionManager + MarketStructure + DealingRange + CHoCH/FVG state.
        """
        o = float(exec_row.get("Open", np.nan))
        h = float(exec_row.get("High", np.nan))
        l = float(exec_row.get("Low", np.nan))
        c = float(exec_row.get("Close", np.nan))

        ctx_bar = exec_row if ("Active_Session" in exec_row.index or m15_bar is None) else m15_bar
        h4_trend = str(h4_bar.get("Trend", "none")) if h4_bar is not None else "none"
        ctx_trend = str(ctx_bar.get("Trend", "none"))
        active_sess = str(ctx_bar.get("Active_Session", "None"))
        in_premium = bool(ctx_bar.get("In_Premium", False))
        in_discount = bool(ctx_bar.get("In_Discount", False))

        asia_h = float(ctx_bar.get("Asia_High", np.nan))
        asia_l = float(ctx_bar.get("Asia_Low", np.nan))
        asia_range_pips = round((asia_h - asia_l) / self.pip_size, 1) if (not np.isnan(asia_h) and not np.isnan(asia_l) and asia_h > asia_l) else 0.0

        diag: Dict[str, Any] = {
            "symbol": self.symbol,
            "strategy": "LEWIS_KELLY_SMC",
            "timeframe": self.tf_exec,
            "open": 0.0 if not np.isfinite(o) else o,
            "high": 0.0 if not np.isfinite(h) else h,
            "low": 0.0 if not np.isfinite(l) else l,
            "close": 0.0 if not np.isfinite(c) else c,
            "volume": float(exec_row.get("Volume", 0.0)),
            "asian_high": 0.0 if np.isnan(asia_h) else asia_h,
            "asian_low": 0.0 if np.isnan(asia_l) else asia_l,
            "asian_range_pips": asia_range_pips,
            "session_active": active_sess,
            "h4_trend": h4_trend,
            "m15_trend": ctx_trend,
            "in_premium": in_premium,
            "in_discount": in_discount,
            "sweep_detected": False,
            "sweep_level": 0.0,
            "swept_pts": 0.0,
            "decision": "REJECT_SETUP",
            "reason": "SMC criteria not aligned",
        }

        if not (np.isfinite(o) and np.isfinite(h) and np.isfinite(l) and np.isfinite(c)):
            self._clear_arming_state()
            diag["decision"] = "REJECT_INVALID_OHLC"
            diag["reason"] = "Non-finite OHLC values on closed bar"
            return None, diag

        cur_day = exec_dt.date()
        fx_day = (exec_dt + pd.Timedelta(hours=7)).date()

        if active_sess not in self.target_sessions:
            self._clear_arming_state()
            self._session_key = None
            self._session_high = np.nan
            self._session_low = np.nan
            diag["decision"] = "REJECT_OUTSIDE_SESSION"
            diag["reason"] = f"Session '{active_sess}' not in {self.symbol} killzones {self.target_sessions}"
            return None, diag

        # Track actual session high/low monotonically (seeded with causal SessionManager bounds for >45m cold starts)
        sess_h_col = "London_High" if active_sess == "London" else "NY_High"
        sess_l_col = "London_Low" if active_sess == "London" else "NY_Low"
        ctx_sess_h = float(ctx_bar.get(sess_h_col, np.nan))
        ctx_sess_l = float(ctx_bar.get(sess_l_col, np.nan))
        cur_sess_h = max(h, ctx_sess_h) if np.isfinite(ctx_sess_h) else h
        cur_sess_l = min(l, ctx_sess_l) if np.isfinite(ctx_sess_l) else l
        sess_key = (cur_day, active_sess)
        if self._session_key != sess_key:
            self._session_key = sess_key
            self._session_high = cur_sess_h
            self._session_low = cur_sess_l
        else:
            self._session_high = max(self._session_high, cur_sess_h)
            self._session_low = min(self._session_low, cur_sess_l)

        # Invalidate already-armed direction if H4 HTF bias flips directly against it
        if self.awaiting_choch == "Short" and h4_trend == "bullish":
            self._clear_arming_state()
        elif self.awaiting_choch == "Long" and h4_trend == "bearish":
            self._clear_arming_state()

        # 1. ADR_5 Asian Range Cap Filter
        if not np.isnan(asia_h) and not np.isnan(asia_l) and asia_h > asia_l:
            asian_range = asia_h - asia_l
            cur_adr = adr_dict.get(fx_day, adr_dict.get(cur_day, default_adr))
            if np.isnan(cur_adr) or cur_adr <= 0:
                cur_adr = default_adr
            if (asian_range / cur_adr) > self.adr_ratio:
                self._clear_arming_state()
                diag["decision"] = "REJECT_ADR_FILTER"
                diag["reason"] = f"Asian range ratio {asian_range / cur_adr:.2f} > {self.adr_ratio:.2f}"
                return None, diag

        # 2. Session Liquidity Sweeps (M5 real-time closed bar)
        if active_sess == "London":
            swept_high = bool(ctx_bar.get("Asia_Ever_Swept_High", False) or ctx_bar.get("Sweep_High", False))
            swept_low = bool(ctx_bar.get("Asia_Ever_Swept_Low", False) or ctx_bar.get("Sweep_Low", False))
            sw_h_lvl = asia_h if not np.isnan(asia_h) else 0.0
            sw_l_lvl = asia_l if not np.isnan(asia_l) else 0.0
        else:  # NY
            swept_high = bool(
                ctx_bar.get("London_Ever_Swept_High", False)
                or ctx_bar.get("Asia_Ever_Swept_High", False)
                or ctx_bar.get("Sweep_High", False)
            )
            swept_low = bool(
                ctx_bar.get("London_Ever_Swept_Low", False)
                or ctx_bar.get("Asia_Ever_Swept_Low", False)
                or ctx_bar.get("Sweep_Low", False)
            )
            lon_h = float(ctx_bar.get("London_High", np.nan))
            lon_l = float(ctx_bar.get("London_Low", np.nan))
            sw_h_lvl = lon_h if not np.isnan(lon_h) else (0.0 if np.isnan(asia_h) else asia_h)
            sw_l_lvl = lon_l if not np.isnan(lon_l) else (0.0 if np.isnan(asia_l) else asia_l)

        # 3. HTF Bias + M5 Trend + Dealing Range + Session Sweep -> Arm/Update Confirmation Window
        short_aligned = (h4_trend != "bullish") and (ctx_trend == "bearish") and in_premium and swept_high
        long_aligned = (h4_trend != "bearish") and (ctx_trend == "bullish") and in_discount and swept_low

        if short_aligned:
            diag["sweep_detected"] = True
            diag["sweep_level"] = sw_h_lvl
            anchor_h = max(h, self._session_high) if np.isfinite(self._session_high) else h
            if self.awaiting_choch != "Short":
                self.awaiting_choch = "Short"
                self.sweep_extreme_price = anchor_h
                self.sweep_time = exec_dt
                self.recent_displacement_fvg = None
            elif anchor_h > self.sweep_extreme_price:
                self.sweep_extreme_price = anchor_h
        elif long_aligned:
            diag["sweep_detected"] = True
            diag["sweep_level"] = sw_l_lvl
            anchor_l = min(l, self._session_low) if np.isfinite(self._session_low) else l
            if self.awaiting_choch != "Long":
                self.awaiting_choch = "Long"
                self.sweep_extreme_price = anchor_l
                self.sweep_time = exec_dt
                self.recent_displacement_fvg = None
            elif anchor_l < self.sweep_extreme_price:
                self.sweep_extreme_price = anchor_l

        if self.awaiting_choch is None:
            reasons = []
            if h4_trend == "bullish" and swept_high:
                reasons.append("H4 Bullish blocks Short")
            if h4_trend == "bearish" and swept_low:
                reasons.append("H4 Bearish blocks Long")
            if ctx_trend == "none":
                reasons.append(f"{self.tf_exec} Trend neutral")
            elif not (short_aligned or long_aligned):
                reasons.append(f"{self.tf_exec}={ctx_trend}, H4={h4_trend}, Prem={in_premium}, Disc={in_discount}, SwH={swept_high}, SwL={swept_low}")
            diag["decision"] = "REJECT_HTF_SMC_FILTER"
            diag["reason"] = "; ".join(reasons) if reasons else "Awaiting HTF/M5 SMC alignment"
            return None, diag

        # 4. Check 45-Minute CHoCH / Displacement FVG Confirmation Window
        if self.sweep_time is not None and (exec_dt - self.sweep_time).total_seconds() > 2700:
            self._clear_arming_state()
            diag["decision"] = "REJECT_CHOCH_TIMEOUT"
            diag["reason"] = "45m M5 CHoCH confirmation window expired"
            return None, diag

        is_short = (self.awaiting_choch == "Short")
        return self._confirm_ltf_signal(
            is_short=is_short,
            exec_row=exec_row,
            exec_dt=exec_dt,
            h4_trend=h4_trend,
            m15_trend=ctx_trend,
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
            label = "Short (M5 Bearish+Premium+Sweep)" if is_short else "Long (M5 Bullish+Discount+Sweep)"
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
        self._clear_arming_state()

        if not np.isfinite(risk_dist) or risk_dist < self.min_risk - 1e-9 or risk_dist > self.max_risk + 1e-9:
            diag["decision"] = "REJECT_RISK_BOUNDS"
            diag["reason"] = f"SL distance {risk_dist / self.pip_size:.1f} pips outside [{self.config['min_risk_pips']}, {self.config['max_risk_pips']}]"
            return None, diag

        sign = -1.0 if is_short else 1.0
        tp1 = entry_p + (sign * 2.50 * risk_dist)
        tp2 = entry_p + (sign * 5.00 * risk_dist)
        tp3 = entry_p + (sign * 8.00 * risk_dist)
        diag["decision"] = f"SIGNAL_{direction_str}"
        diag["reason"] = f"SMC {direction_str.title()} confirmed (H4={h4_trend}, M5={m15_trend}, CHoCH/FVG)"
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
        df_m15: Optional[pd.DataFrame],
        df_exec: pd.DataFrame,
    ) -> Tuple[Optional[LKTradeSignal], Dict[str, Any]]:
        """
        Live evaluation entry point using strictly closed H4 and M5 bars (M15 used for ADR_5).
        """
        if df_h4 is None or df_exec is None or len(df_h4) < 10 or len(df_exec) < 5:
            return None, {
                "symbol": self.symbol,
                "strategy": "LEWIS_KELLY_SMC",
                "timeframe": self.tf_exec,
                "decision": "REJECT_INSUFFICIENT_DATA",
                "reason": "Insufficient closed H4/M5 bars",
            }

        h4_prep, _, exec_prep, adr_dict, default_adr = self.prepare_frames(df_h4, df_m15, df_exec)
        exec_dt = exec_prep.index[-1]

        if self.last_evaluated_exec_ts is not None and exec_dt <= self.last_evaluated_exec_ts:
            return None, {
                "symbol": self.symbol,
                "strategy": "LEWIS_KELLY_SMC",
                "timeframe": self.tf_exec,
                "decision": "REJECT_DUPLICATE_BAR",
                "reason": f"Bar {exec_dt} already evaluated",
            }

        def _closed_h4_bar(target_exec_dt: pd.Timestamp) -> Optional[pd.Series]:
            close_dt = target_exec_dt + pd.Timedelta(minutes=self.tf_minutes)
            h4_sub = h4_prep[h4_prep.index <= close_dt - pd.Timedelta(hours=4)]
            return h4_sub.iloc[-1] if not h4_sub.empty else None

        # Synchronize session extremes & 45m arming state across recent bars on cold start or post-trade gaps
        replay_start = exec_dt - pd.Timedelta(minutes=45)
        if self.last_evaluated_exec_ts is None:
            missed_bars = exec_prep[(exec_prep.index >= replay_start) & (exec_prep.index < exec_dt)]
        else:
            since_ts = max(self.last_evaluated_exec_ts, replay_start)
            missed_bars = exec_prep[(exec_prep.index > since_ts) & (exec_prep.index < exec_dt)]

        for prev_dt, prev_row in missed_bars.iterrows():
            h4_prev = _closed_h4_bar(prev_dt)
            self.evaluate_bar_state(prev_row, prev_dt, None, h4_prev, adr_dict, default_adr)

        h4_bar = _closed_h4_bar(exec_dt)
        exec_row = exec_prep.iloc[-1]
        self.last_evaluated_exec_ts = exec_dt
        return self.evaluate_bar_state(exec_row, exec_dt, None, h4_bar, adr_dict, default_adr)
