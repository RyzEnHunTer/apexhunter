"""
Institutional Market Calculation & Surveillance Forensic Logging System
========================================================================
Location: core/audit_logger.py

Provides comprehensive forensic auditing and live debugging:
1. MarketAuditLogger:
   - Structured daily CSV: logs/market_calculations_YYYYMMDD.csv
     Records every 5M & 15M candle evaluation across all 8 assets with exact
     indicators, sweep levels, decisions, and guard rejection reasons for 1-to-1 backtest parity.
   - Chronological stream: logs/market_surveillance_YYYYMMDD.log
     Human-readable real-time event journal of market state, setups, and trades.
2. EngineLogger:
   - System execution event logger: logs/live_engine_YYYYMMDD.log
     Captures broker order placement, fills, tickets, breakeven shifts,
     circuit breaker events, news auto-closes, and full exception tracebacks.
3. LiveStateExporter:
   - logs/live_state.json: Atomic snapshot of bot state, open positions, CB room,
     and surveillance indicators for external dashboards or health monitors.
"""

import os
import sys
import csv
import json
import traceback
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TZ_IST = timezone(timedelta(hours=5, minutes=30))

class MarketAuditLogger:
    """
    Forensic Market Calculation Logger for Dual-Engine Live Architecture.
    Records every closed candle calculation across:
      - ApexHunter v1.2.1: Gold (XAUUSD) & Nasdaq (NAS100) on M5
      - Lewis Kelly SMC: 6 Forex Pairs on M15
    """
    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            self.log_dir = os.path.join(PROJECT_ROOT, "logs")
        else:
            self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

        self.csv_headers = [
            "timestamp_utc", "timestamp_ist", "symbol", "strategy", "timeframe",
            "open", "high", "low", "close", "volume",
            "ema9_5m", "ema20_5m", "ema_gap", "vwap_5m",
            "bias_1h", "ema9_1h", "ema20_1h", "adx_1h", "atr_1h", "h1_ema20_level",
            "stretch_ratio", "chase_ratio",
            "asian_high", "asian_low", "asian_range_pips", "session_active",
            "sweep_detected", "sweep_level", "swept_pts",
            "decision", "reason",
            "planned_entry", "planned_sl", "planned_tp1", "planned_tp2", "planned_tp3", "planned_lots"
        ]

    def log_candle(self, data: Dict[str, Any]):
        """Logs a single candle evaluation record to both structured CSV and text surveillance log."""
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc.astimezone(TZ_IST)
        date_str = now_utc.strftime("%Y%m%d")

        if not data.get("timestamp_utc"):
            data["timestamp_utc"] = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        if not data.get("timestamp_ist"):
            data["timestamp_ist"] = now_ist.strftime("%Y-%m-%d %H:%M:%S")

        csv_path = os.path.join(self.log_dir, f"market_calculations_{date_str}.csv")
        txt_path = os.path.join(self.log_dir, f"market_surveillance_{date_str}.log")

        # 1. Write structured CSV row
        file_exists = os.path.exists(csv_path)
        try:
            with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.csv_headers, extrasaction="ignore")
                if not file_exists:
                    writer.writeheader()
                writer.writerow(data)
        except Exception as e:
            # Silent fallback to prevent logging failure from disturbing trading loop
            pass

        # 2. Write human-readable surveillance line
        try:
            strat = data.get("strategy", "")
            sym = data.get("symbol", "")
            close_val = data.get("close", 0.0)
            tf = data.get("timeframe", "M5")
            dec = data.get("decision", "")
            reason = data.get("reason", "")

            if "APEX" in strat or sym in ["XAUUSD", "NAS100"]:
                details = (
                    f"EMA9: {data.get('ema9_5m', 0.0):.2f} | EMA20: {data.get('ema20_5m', 0.0):.2f} | "
                    f"Gap: {data.get('ema_gap', 0.0):.2f} | Bias: {data.get('bias_1h', 0):+d} | "
                    f"ADX: {data.get('adx_1h', 0.0):.1f} | ATR: {data.get('atr_1h', 0.0):.2f} | "
                    f"Stretch: {data.get('stretch_ratio', 0.0):.2f} | Chase: {data.get('chase_ratio', 0.0):.2f} | "
                    f"Sweep: {data.get('sweep_detected', False)}"
                )
            else:
                details = (
                    f"AsianH: {data.get('asian_high', 0.0):.5f} | AsianL: {data.get('asian_low', 0.0):.5f} | "
                    f"Sess: {data.get('session_active', '')} | "
                    f"Sweep: {data.get('sweep_detected', False)} (Lvl: {data.get('sweep_level', 0.0):.5f})"
                )

            log_line = (
                f"[{data.get('timestamp_utc')} UTC | {data.get('timestamp_ist')} IST] "
                f"[{strat}] {sym} ({tf}) | C: {close_val} | {details} | "
                f"Status: {dec} ({reason})\n"
            )

            with open(txt_path, mode="a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass


class EngineLogger:
    """
    Application & Execution Event Logger.
    Records system events, MT5 order executions, trailing SL shifts, circuit breaker actions,
    and complete stack traces for runtime errors.
    """
    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            self.log_dir = os.path.join(PROJECT_ROOT, "logs")
        else:
            self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

    def _write_log(self, level: str, msg: str, component: str = "MasterBot", exc_info: bool = False):
        now_utc = datetime.now(timezone.utc)
        date_str = now_utc.strftime("%Y%m%d")
        log_file = os.path.join(self.log_dir, f"live_engine_{date_str}.log")

        time_str = now_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"{time_str} UTC [{level}] [{component}] {msg}\n"

        if exc_info:
            line += traceback.format_exc() + "\n"

        try:
            with open(log_file, mode="a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def info(self, msg: str, component: str = "MasterBot"):
        self._write_log("INFO", msg, component)

    def warning(self, msg: str, component: str = "MasterBot"):
        self._write_log("WARNING", msg, component)

    def trade(self, msg: str, component: str = "Execution"):
        self._write_log("TRADE", msg, component)

    def error(self, msg: str, exc_info: bool = True, component: str = "MasterBot"):
        self._write_log("ERROR", msg, component, exc_info=exc_info)

    def critical(self, msg: str, exc_info: bool = True, component: str = "RiskGuardian"):
        self._write_log("CRITICAL", msg, component, exc_info=exc_info)

    def debug(self, msg: str, component: str = "MasterBot"):
        self._write_log("DEBUG", msg, component)


class LiveStateExporter:
    """
    Exports atomic real-time snapshots of the bot's state to logs/live_state.json
    for live surveillance dashboards, telegram status bots, and VPS monitoring.
    """
    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            self.log_dir = os.path.join(PROJECT_ROOT, "logs")
        else:
            self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.state_file = os.path.join(self.log_dir, "live_state.json")

    def export_state(self, state_dict: Dict[str, Any]):
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc.astimezone(TZ_IST)
        state_dict["updated_utc"] = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        state_dict["updated_ist"] = now_ist.strftime("%Y-%m-%d %H:%M:%S")

        temp_file = self.state_file + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state_dict, f, indent=2, default=str)
            # Atomic replace prevents file read corruption
            os.replace(temp_file, self.state_file)
        except Exception:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
