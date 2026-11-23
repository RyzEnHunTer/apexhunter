"""
Master Institutional Unified Bot (Simultaneous Dual-Engine Live Runner)
========================================================================
Location: master_live_bot.py

Runs BOTH proven institutional strategies SIMULTANEOUSLY and INDEPENDENTLY.
Architecture Note:
  As confirmed in our empirical audits, NO Market Analyser and NO Regime Switcher
  is utilized. Both strategies operate simultaneously on their dedicated assets:
  1. DCC ApexHunter v1.2.1: Gold (XAUUSD) & Nasdaq (NAS100) on M5 closed bars
  2. Lewis Kelly SMC Basket: 6 Pairs (EURUSD, GBPUSD, USDJPY, AUDUSD, EURJPY, USDCAD) on M15 closed bars

Guaranteed Safeguards & Production Architecture:
- Dual-Circuit Breakers: 4.0% Daily CB (fitted risk), 8.5% Floor CB ($4,575 floor with 0.20% recovery buffer)
- News Guardian: Auto-closes open positions 10m before High-Impact news; 12h calendar auto-refresh; 20m/15m embargo
- Spread Guardian: Golden Numbers absolute caps + max 30% SL ratio filter
- Server-Side Split Orders: 3-way split for Forex (30% @ 2.5R, 30% @ 5.0R, 40% @ 8.0R), 2-way split for Apex (50% @ 1.4R, 50% @ 2.1R)
- Anti-Repaint Gating: Evaluates signals strictly on 100% closed candles
- Weekend Risk Shield: Auto-liquidates open positions Friday 20:00 UTC before weekend gaps
- Crash Recovery: Reconstructs position groups from live MT5 tickets upon bot restart
- Remote Alerts: Asynchronous Telegram & Discord notifications for all trade lifecycle events

Usage:
  python master_live_bot.py           # Paper Trading / Dry-Run Mode (Safe Forward Test)
  python master_live_bot.py --live    # Live MT5 Order Execution Mode
"""

import os
import sys
import time
import json
import argparse
import zoneinfo
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any
from rich.live import Live
from rich.text import Text
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.mt5_live_bridge import MT5LiveBridge
from core.risk_guardian import RiskGuardian
from core.spread_guardian import SpreadGuardian
from core.news_guardian import NewsGuardian
from core.order_manager import OrderManager
from core.account_config_manager import AccountConfigManager
from core.apexhunter_engine import DCCEngineV12, SignalType
from core.audit_logger import MarketAuditLogger, EngineLogger, LiveStateExporter
from live.notifier import TradeNotifier
from core.ui_renderer import (
    console,
    render_institutional_control_center,
    render_lifecycle_menu,
    render_notifications_menu,
    render_economic_calendar_table,
    render_live_spreads_table,
    render_telemetry_dashboard
)

def format_symbol_price(symbol: str, price: float) -> str:
    """Formats asset price according to standard market quote conventions."""
    if "JPY" in symbol:
        return f"{price:.3f}"
    elif "XAU" in symbol or "GOLD" in symbol or "NAS" in symbol or "USTEC" in symbol:
        return f"{price:.2f}"
    return f"{price:.5f}"

class MasterUnifiedBot:
    def __init__(
        self,
        dry_run: bool = True,
        base_capital: Optional[float] = None,
        max_concurrent_trades: Optional[int] = None,
        magic_base: Optional[int] = None
    ):
        self.dry_run = dry_run

        # Load centralized configuration
        config_path = os.path.join(PROJECT_ROOT, "live", "config.json")
        cfg: Dict[str, Any] = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except Exception as e:
                print(f"[MasterUnifiedBot WARN] Failed to load config.json: {e}")

        tr_cfg = cfg.get("trading_rules", {})
        news_cfg = cfg.get("news_guardian", {})
        spread_cfg = cfg.get("spread_guardian", {})

        self.base_capital = base_capital if base_capital is not None else tr_cfg.get("base_capital", 5000.0)
        self.max_concurrent_trades = max_concurrent_trades if max_concurrent_trades is not None else tr_cfg.get("max_concurrent_trades", 4)
        self.magic_base = magic_base if magic_base is not None else tr_cfg.get("magic_base", 88000)
        broker_offset_hours = tr_cfg.get("broker_utc_offset_hours", 2)

        # 1. MT5 Live Data Bridge
        self.bridge = MT5LiveBridge(
            target_symbols=[
                'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'EURJPY', 'USDCAD',
                'XAUUSD', 'NAS100'
            ],
            broker_offset_hours=broker_offset_hours
        )

        # 2. Institutional Risk Guardian (Dual Circuit Breakers)
        self.risk_guardian = RiskGuardian(
            baseline_capital=self.base_capital,
            daily_cb_pct=tr_cfg.get("daily_cb_pct", 0.040),      # 4.0% Daily CB ($200)
            max_floor_cb_pct=tr_cfg.get("max_floor_cb_pct", 0.085),  # 8.5% Floor CB ($425 drop / $4,575 balance)
            hard_floor_pct=tr_cfg.get("hard_floor_pct", 0.100),    # 10.0% Hard Breach Floor ($500 drop / $4,500 balance)
            normal_risk_pct=tr_cfg.get("normal_risk_pct", 0.010),   # 1.00% Normal Risk ($50 on $5,000)
            recovery_risk_pct=tr_cfg.get("recovery_risk_pct", 0.002)  # 0.20% Recovery Risk in 1.5% buffer (~$9-$10)
        )

        # 3. Spread Guardian (Calibrated Golden Numbers)
        self.spread_guardian = SpreadGuardian(
            max_spread_sl_ratio=spread_cfg.get("max_spread_sl_ratio", 0.30),
            custom_caps=spread_cfg.get("custom_caps")
        )

        # 4. High-Impact News Guardian (12h auto-refresh + 10m auto-close)
        self.news_guardian = NewsGuardian(
            refresh_interval_hours=news_cfg.get("calendar_refresh_hours", 12)
        )
        self.news_auto_close_min = news_cfg.get("auto_close_before_min", 10)
        self.news_embargo_before_min = news_cfg.get("embargo_before_min", 20)
        self.news_embargo_after_min = news_cfg.get("embargo_after_min", 15)

        # 5. Server-Side Order Split & Breakeven Manager
        self.order_manager = OrderManager(magic_base=self.magic_base)

        # 6. Remote Notifier
        self.notifier = TradeNotifier(config_path=config_path)

        # 7. ApexHunter 1.2.1 Engines for Gold & Nasdaq (Simultaneous Parallel Execution)
        self.apex_engines = {
            'XAUUSD': DCCEngineV12(symbol='XAUUSD', atr_sl_multiplier=0.90, tp1_rr=1.40, tp2_rr=2.20),
            'NAS100': DCCEngineV12(symbol='NAS100', atr_sl_multiplier=1.40, tp1_rr=1.40, tp2_rr=2.10)
        }

        # 8. Persistent Account Lifecycle & Evaluation Target Manager
        self.acc_mgr = AccountConfigManager()
        self.acc_cfg: Dict[str, Any] = {}
        self.challenge_target_reached: bool = False
        self._cb_notified_today: bool = False

        # Internal position tracking
        self.open_positions: Dict[str, Dict[str, Any]] = {}
        self.last_dashboard_time: float = 0.0

        # 9. Terminal Animation & Surveillance Heartbeat
        self.tz_ist = timezone(timedelta(hours=5, minutes=30))
        self.tz_ny = zoneinfo.ZoneInfo("America/New_York")
        self.tz_london = zoneinfo.ZoneInfo("Europe/London")
        self.spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.spinner_colors = [
            "bold bright_cyan",
            "bold cyan",
            "bold bright_green",
            "bold green",
            "bold bright_yellow",
            "bold bright_magenta"
        ]
        self.spinner_idx: int = 0
        self.live: Optional[Live] = None

        # 10. Institutional Forensic Audit & Execution Event Loggers
        self.audit_logger = MarketAuditLogger()
        self.logger = EngineLogger()
        self.state_exporter = LiveStateExporter()
        self.last_state_export: float = 0.0

        # 11. Real-Time Session & Alert Surveillance State
        self.current_session_state: str = "INITIALIZING"
        self.entry_start_hour_utc: int = 6
        self.entry_end_hour_utc: int = 19
        self.last_daily_summary_date: Optional[Any] = None

    def get_session_state(self, now_utc: datetime) -> str:
        """Returns the trading window / killzone state for a given UTC datetime."""
        if self.is_weekend_market_closed(now_utc):
            return "PAUSED_WEEKEND"

        h = now_utc.hour
        if h == 9:
            return "PAUSED_TRAP_09"
        elif h == 13:
            return "PAUSED_TRAP_13"
        elif h < self.entry_start_hour_utc or h >= self.entry_end_hour_utc:
            return "PAUSED_ASIAN"
        elif self.entry_start_hour_utc <= h < 12:
            return "ACTIVE_LONDON"
        else:
            return "ACTIVE_NY"

    def check_session_and_trap_alerts(self, now_utc: datetime, is_startup: bool = False):
        """Monitors and broadcasts trading pause and resume milestones across killzones, trap hours, and sessions."""
        new_state = self.get_session_state(now_utc)

        if is_startup:
            self.current_session_state = new_state
            if new_state == "PAUSED_WEEKEND":
                console.print("\n[bold yellow][STATUS: PAUSED - WEEKEND MARKET CLOSED][/] Resumes: Monday 06:00 UTC (11:30 IST)")
                self.notifier.notify_trading_paused(
                    zone_title="Weekend Market Closed (Saturday & Sunday)",
                    reason="Bot started during weekend market closure. Forex & CFD markets are closed.",
                    resume_time_str="Monday 06:00 UTC (11:30 IST) - London Session Open",
                    is_startup=True
                )
            elif new_state == "PAUSED_TRAP_09":
                console.print("\n[bold yellow][STATUS: PAUSED IN MORNING DEAD TRAP HOUR][/] Resumes: 10:00 UTC (15:30 IST)")
                self.notifier.notify_trading_paused(
                    zone_title="Morning Dead Trap Hour / Killzone Pause (09:00-10:00 UTC / 14:30-15:30 IST)",
                    reason="Bot started during London midday lull. Scanning suspended to avoid false breakout traps.",
                    resume_time_str="10:00 UTC (15:30 IST)",
                    is_startup=True
                )
            elif new_state == "PAUSED_TRAP_13":
                console.print("\n[bold yellow][STATUS: PAUSED IN US DEAD TRAP HOUR][/] Resumes: 14:00 UTC (19:30 IST)")
                self.notifier.notify_trading_paused(
                    zone_title="US Dead Trap Hour / Killzone Pause (13:00-14:00 UTC / 18:30-19:30 IST)",
                    reason="Bot started during US pre-market transition chop. Trade arming and execution suspended.",
                    resume_time_str="14:00 UTC (19:30 IST)",
                    is_startup=True
                )
            elif new_state == "PAUSED_ASIAN":
                console.print("\n[bold yellow][STATUS: PAUSED IN ASIAN RANGE FORMATION][/] Resumes: 06:00 UTC (11:30 IST)")
                self.notifier.notify_trading_paused(
                    zone_title="Asian Session Range Formation (19:00-06:00 UTC / 00:30-11:30 IST)",
                    reason="Bot started outside active trading window. Asian session is designated for liquidity range building (entries disabled).",
                    resume_time_str="06:00 UTC (11:30 IST) - London Session Open",
                    is_startup=True
                )
            return

        if new_state == self.current_session_state:
            return

        prev_state = self.current_session_state
        self.current_session_state = new_state

        if new_state == "PAUSED_WEEKEND":
            console.print("\n[bold yellow][TRADING PAUSED: WEEKEND MARKET CLOSED][/] Forex & CFD markets closed for the weekend.")
            self.notifier.notify_trading_paused(
                zone_title="Weekend Market Closed (Saturday & Sunday)",
                reason="Forex & CFD markets closed for the weekend. All scanning and execution suspended.",
                resume_time_str="Monday 06:00 UTC (11:30 IST) - London Session Open"
            )
        elif new_state == "PAUSED_TRAP_09":
            console.print("\n[bold yellow][TRADING PAUSED: MORNING DEAD TRAP HOUR (09:00 UTC / 14:30 IST)][/]")
            self.notifier.notify_trading_paused(
                zone_title="Morning Dead Trap Hour / Killzone Pause (09:00-10:00 UTC / 14:30-15:30 IST)",
                reason="London midday liquidity lull. High risk of false breakout traps.",
                resume_time_str="10:00 UTC (15:30 IST)"
            )
        elif new_state == "PAUSED_TRAP_13":
            console.print("\n[bold yellow][TRADING PAUSED: US DEAD TRAP HOUR (13:00 UTC / 18:30 IST)][/]")
            self.notifier.notify_trading_paused(
                zone_title="US Dead Trap Hour / Killzone Pause (13:00-14:00 UTC / 18:30-19:30 IST)",
                reason="US pre-market transition chop. Trade arming and execution suspended.",
                resume_time_str="14:00 UTC (19:30 IST)"
            )
        elif new_state == "PAUSED_ASIAN":
            console.print("\n[bold yellow][TRADING PAUSED: END-OF-DAY / ASIAN RANGE FORMATION][/]")
            self.notifier.notify_trading_paused(
                zone_title="End-of-Day / Asian Session Range Formation (19:00-06:00 UTC)",
                reason="Active trading session closed. Asian session is designated for liquidity range building (entries disabled).",
                resume_time_str="06:00 UTC (11:30 IST) - London Session Open"
            )
        elif new_state == "ACTIVE_LONDON":
            if prev_state in ["PAUSED_ASIAN", "PAUSED_WEEKEND"]:
                console.print("\n[bold bright_green][TRADING RESUMED: LONDON SESSION OPEN (06:00 UTC / 11:30 IST)][/]")
                self.notifier.notify_trading_resumed(
                    zone_title="London Session Opened (06:00 UTC / 11:30 IST)",
                    session_name="European / London Killzone Window",
                    details="Weekend closed / Asian range concluded. Active market surveillance and trade execution restored."
                )
            elif prev_state == "PAUSED_TRAP_09":
                console.print("\n[bold bright_green][TRADING RESUMED: MORNING DEAD TRAP HOUR ENDED (10:00 UTC / 15:30 IST)][/]")
                self.notifier.notify_trading_resumed(
                    zone_title="Morning Dead Trap Hour Ended (10:00 UTC / 15:30 IST)",
                    session_name="Pre-New York Window",
                    details="Midday trap period concluded. Active market surveillance and trade execution restored."
                )
        elif new_state == "ACTIVE_NY":
            if prev_state == "ACTIVE_LONDON":
                console.print("\n[bold bright_blue][SESSION UPDATE: NEW YORK SESSION OPEN (12:00 UTC / 17:30 IST)][/]")
                self.notifier.notify_session(
                    session_name="New York Session (12:00 UTC / 17:30 IST)",
                    status="OPENED - US Killzone Window Active"
                )
            elif prev_state == "PAUSED_TRAP_13":
                console.print("\n[bold bright_green][TRADING RESUMED: US DEAD TRAP HOUR ENDED (14:00 UTC / 19:30 IST)][/]")
                self.notifier.notify_trading_resumed(
                    zone_title="US Dead Trap Hour Ended (14:00 UTC / 19:30 IST)",
                    session_name="New York Active Trading Session",
                    details="US pre-market transition ended. Active market surveillance restored."
                )

    def _run_daily_summary(self, audit_date: Any):
        """Compiles end-of-day scorecard at 21:05 UTC (market close) and dispatches via notifier."""
        try:
            now_utc = datetime.now(timezone.utc)
            acc = mt5.account_info()
            cur_equity = float(acc.equity) if acc else self.risk_guardian.day_start_balance
            cur_balance = float(acc.balance) if acc else self.risk_guardian.day_start_balance
            start_bal = self.risk_guardian.day_start_balance if self.risk_guardian.day_start_balance > 0 else cur_balance
            day_pnl = cur_balance - start_bal
            day_pnl_pct = (day_pnl / start_bal * 100.0) if start_bal > 0 else 0.0
            cur_daily_loss = max(0.0, -day_pnl)
            daily_dd_pct = (cur_daily_loss / start_bal * 100.0) if start_bal > 0 else 0.0
            max_allowed_loss = start_bal * self.risk_guardian.daily_cb_pct
            cushion_remaining = max(0.0, max_allowed_loss - cur_daily_loss)

            day_start_dt = datetime(audit_date.year, audit_date.month, audit_date.day, 0, 0, tzinfo=timezone.utc)
            deals = mt5.history_deals_get(day_start_dt, now_utc)
            closed_deals = []
            win_count = 0
            loss_count = 0

            if deals:
                exit_deals = [d for d in deals if d.entry == 1]
                for d in exit_deals:
                    p = float(d.profit + d.commission + d.swap)
                    if p > 0:
                        win_count += 1
                    elif p < 0:
                        loss_count += 1
                    dir_str = "BUY" if d.type == 0 else "SELL"
                    closed_deals.append({
                        "ticket": d.ticket,
                        "symbol": d.symbol,
                        "type": dir_str,
                        "volume": d.volume,
                        "profit": round(p, 2),
                        "comment": d.comment
                    })

            self.notifier.notify_daily_summary(
                date_str=audit_date.strftime("%Y-%m-%d"),
                starting_equity=start_bal,
                closing_equity=cur_equity,
                balance=cur_balance,
                trades_count=len(closed_deals),
                winning_trades=win_count,
                losing_trades=loss_count,
                daily_pnl=round(day_pnl, 2),
                daily_pnl_pct=round(day_pnl_pct, 2),
                daily_dd_pct=round(daily_dd_pct, 2),
                cushion_remaining=round(cushion_remaining, 2),
                closed_trades_details=closed_deals
            )
            self.logger.info(f"[DAILY SCORECARD DISPATCHED] Date: {audit_date}, Realized PnL: ${day_pnl:,.2f} ({day_pnl_pct:+.2f}%), Trades: {len(closed_deals)}")
        except Exception as e:
            self.logger.error(f"[DAILY SCORECARD ERROR] Failed to dispatch daily summary: {e}")

    def get_dynamic_market_close_hour_utc(self, dt: Optional[datetime] = None) -> int:
        """Dynamically returns 21 in Summer (EDT) and 22 in Winter (EST) via America/New_York DST anchor."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        ny_dt = dt.astimezone(self.tz_ny)
        return 21 if bool(ny_dt.dst()) else 22

    def is_friday_weekend_close_window(self, now_utc: datetime) -> bool:
        """
        Detects Friday 16:45 NY Time (15 minutes before 17:00 NY close),
        automatically accounting for Daylight Saving Time (DST) seasonal transitions.
        Triggers at 20:45 UTC in Summer (EDT) and 21:45 UTC in Winter (EST).
        """
        now_ny = now_utc.astimezone(self.tz_ny)
        return (now_ny.weekday() == 4 and (now_ny.hour > 16 or (now_ny.hour == 16 and now_ny.minute >= 45)))

    def is_weekend_market_closed(self, now_utc: datetime) -> bool:
        """
        Market is closed from Friday 17:00 NY Time until Sunday 17:00 NY Time.
        Automatically accounts for Winter (EST) and Summer (EDT) transitions.
        """
        now_ny = now_utc.astimezone(self.tz_ny)
        if now_ny.weekday() == 4 and now_ny.hour >= 17:
            return True
        if now_ny.weekday() == 5:
            return True
        if now_ny.weekday() == 6 and now_ny.hour < 17:
            return True
        return False

    def get_spinner_frame(self) -> Tuple[str, str]:
        """Returns the next rotating spinner frame and color."""
        frame = self.spinner_frames[self.spinner_idx % len(self.spinner_frames)]
        color = self.spinner_colors[self.spinner_idx % len(self.spinner_colors)]
        self.spinner_idx += 1
        return frame, color

    def calculate_candle_countdowns(self, now_utc: datetime) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Calculates exact minutes and seconds remaining until the close of the current:
        1. M5 candle (ApexHunter: Gold XAUUSD & Nasdaq NAS100)
        2. M15 candle (Lewis Kelly: Forex Basket)
        Assigns urgency color styles:
          - > 60s / 120s: bold green
          - 30s-60s / 45s-120s: bold yellow
          - <= 30s / <= 45s: bold bright_red (imminent candle close)
        """
        # M5 Countdown (300 seconds)
        seconds_into_5m = (now_utc.minute % 5) * 60 + now_utc.second + (now_utc.microsecond / 1_000_000.0)
        seconds_left_in_5m = max(0.0, 300.0 - seconds_into_5m)
        m_5m = int(seconds_left_in_5m // 60)
        s_5m = int(seconds_left_in_5m % 60)
        if seconds_left_in_5m > 60.0:
            style_5m = "bold green"
        elif seconds_left_in_5m > 30.0:
            style_5m = "bold yellow"
        else:
            style_5m = "bold bright_red"

        # M15 Countdown (900 seconds)
        seconds_into_15m = (now_utc.minute % 15) * 60 + now_utc.second + (now_utc.microsecond / 1_000_000.0)
        seconds_left_in_15m = max(0.0, 900.0 - seconds_into_15m)
        m_15m = int(seconds_left_in_15m // 60)
        s_15m = int(seconds_left_in_15m % 60)
        if seconds_left_in_15m > 120.0:
            style_15m = "bold green"
        elif seconds_left_in_15m > 45.0:
            style_15m = "bold yellow"
        else:
            style_15m = "bold bright_red"

        cd_5m = {
            "seconds_left": seconds_left_in_5m,
            "minutes": m_5m,
            "seconds": s_5m,
            "color_style": style_5m
        }
        cd_15m = {
            "seconds_left": seconds_left_in_15m,
            "minutes": m_15m,
            "seconds": s_15m,
            "color_style": style_15m
        }
        return cd_5m, cd_15m

    def build_surveillance_status(self, now_utc: datetime, acc: Optional[Any] = None) -> Text:
        """
        Builds the Rich Text surveillance heartbeat status line with:
        1. Rotating radar surveillance spinner
        2. Execution mode badge (LIVE MT5 / PAPER)
        3. Dual timestamps: UTC and IST
        4. Primary asset live spreads (XAUUSD, NAS100, EURUSD) colored vs Golden Caps
        5. Exact countdowns to next M5 & M15 bar closes with urgency colors
        6. Account equity and today PnL
        7. Real-time status badges (Target locked, Weekend close, News shield, Open positions, Hunting)
        """
        now_ist = now_utc.astimezone(self.tz_ist)
        frame, frame_color = self.get_spinner_frame()
        cd_5m, cd_15m = self.calculate_candle_countdowns(now_utc)

        t = Text()

        # 1. Rotating Radar Surveillance Badge & Execution Mode
        t.append("[", style="bold cyan")
        t.append("SURVEILLANCE ", style="bold bright_white")
        t.append(frame, style=frame_color)
        t.append("] ", style="bold cyan")

        mode_str = "LIVE MT5" if not self.dry_run else "PAPER"
        mode_style = "bold bright_green" if not self.dry_run else "bold bright_yellow"
        t.append(f"[{mode_str}] ", style=mode_style)

        # 2. Dual Timezone Timestamps
        t.append(f"{now_utc.strftime('%H:%M:%S')} UTC ", style="bold white")
        t.append(f"({now_ist.strftime('%H:%M:%S')} IST)", style="bold bright_cyan")
        t.append(" | ", style="bright_black")

        # 3. Rotating 8-Asset Live Spreads (3 groups cycling every 3 seconds)
        cycle_slot = (now_utc.second // 3) % 3
        if cycle_slot == 0:
            active_group = [('XAUUSD', 'XAU', '$', 0.40, 0.65), ('NAS100', 'NAS', 'pt', 2.5, 7.0), ('EURUSD', 'EUR', 'p', 1.2, 2.2)]
            group_label = "A1"
        elif cycle_slot == 1:
            active_group = [('GBPUSD', 'GBP', 'p', 1.5, 2.5), ('USDJPY', 'JPY', 'p', 1.2, 2.0), ('AUDUSD', 'AUD', 'p', 1.4, 2.5)]
            group_label = "A2"
        else:
            active_group = [('USDCAD', 'CAD', 'p', 1.5, 2.8), ('EURJPY', 'EURJPY', 'p', 1.8, 3.0)]
            group_label = "A3"

        t.append(f"[{group_label}: ", style="bold bright_black")
        for i, (sym, short_label, unit, g_green, g_yellow) in enumerate(active_group):
            broker_sym = self.bridge.resolved_symbols.get(sym, sym)
            tick = mt5.symbol_info_tick(broker_sym)
            if tick and tick.ask > tick.bid:
                mult = self.spread_guardian.get_pip_multiplier(sym)
                sp_val = (tick.ask - tick.bid) * mult
                if unit == '$':
                    sp_str = f"${sp_val:.2f}"
                elif unit == 'pt':
                    sp_str = f"{sp_val:.1f}pt"
                else:
                    sp_str = f"{sp_val:.1f}p"

                style_sp = "bold green" if sp_val <= g_green else ("bold yellow" if sp_val <= g_yellow else "bold red")
                t.append(f"{short_label}: ", style="bold white")
                t.append(sp_str, style=style_sp)
            else:
                t.append(f"{short_label}: N/A", style="dim white")

            if i < len(active_group) - 1:
                t.append(" · ", style="bright_black")

        if cycle_slot == 2:
            t.append(" · ", style="bright_black")
            t.append("8/8 OK", style="bold bright_green")
        t.append("] | ", style="bright_black")

        # 4. Next Bar Countdowns (M5 & M15)
        t.append("Bars in: ", style="bold white")
        t.append(f"M5 {cd_5m['minutes']:02d}m {cd_5m['seconds']:02d}s", style=cd_5m['color_style'])
        t.append(" · ", style="bright_black")
        t.append(f"M15 {cd_15m['minutes']:02d}m {cd_15m['seconds']:02d}s", style=cd_15m['color_style'])
        t.append(" | ", style="bright_black")

        # 5. Account Equity & Today PnL
        if acc:
            eq_val = f"${acc.equity:,.2f}"
            pnl_day = acc.equity - self.risk_guardian.day_start_balance
            pnl_str = f"+${pnl_day:,.2f}" if pnl_day >= 0 else f"-${abs(pnl_day):,.2f}"
            pnl_style = "bold bright_green" if pnl_day >= 0 else "bold bright_red"
        else:
            eq_val = f"${self.base_capital:,.2f}"
            pnl_str = "+$0.00"
            pnl_style = "bold white"

        t.append("Eq: ", style="bold white")
        t.append(eq_val, style="bold bright_green")
        t.append(f" ({pnl_str})", style=pnl_style)

        # 6. Dynamic Badges
        is_weekend = self.is_weekend_market_closed(now_utc)
        if is_weekend:
            day_name = now_utc.strftime('%a').upper()
            close_utc_h = self.get_dynamic_market_close_hour_utc(now_utc)
            t.append(" | ", style="bright_black")
            t.append(f"[WEEKEND ({day_name}) MARKET CLOSED - RESUMES SUN {close_utc_h}:00 UTC / 17:00 NY]", style="bold bright_yellow")
        elif self.challenge_target_reached:
            t.append(" | ", style="bright_black")
            t.append("[🏆 TARGET LOCKED - TRADING HALTED]", style="bold bright_green")
        elif self.risk_guardian.daily_halt_active:
            t.append(" | ", style="bright_black")
            t.append("[🛑 DAILY CIRCUIT BREAKER HALTED]", style="bold bright_red")
        elif self.open_positions:
            pos_labels = [f"{sym} {p.get('direction', '')}" for sym, p in self.open_positions.items()]
            t.append(" | ", style="bright_black")
            t.append(f"[POS: {', '.join(pos_labels)}]", style="bold bright_green")
        else:
            # Check news embargo on Gold or EUR
            is_emb, emb_reason = self.news_guardian.is_news_embargo('XAUUSD', now_utc, self.news_embargo_before_min, self.news_embargo_after_min)
            if is_emb:
                t.append(" | ", style="bright_black")
                t.append(f"[🛡️ NEWS SHIELD: {emb_reason}]", style="bold bright_red")
            else:
                t.append(" | ", style="bright_black")
                t.append("[🟢 HUNTING 8/8 ASSETS: 2 APEX + 6 FOREX]", style="bold green")

        return t

    def connect_and_check(self) -> bool:
        """Connects to MT5, resolves broker symbols, and establishes baseline anchors."""
        if not self.bridge.connect():
            console.print("[bold red][CRITICAL][/] Could not connect to MetaTrader 5!")
            return False

        acc = mt5.account_info()
        if not acc:
            console.print("[bold red][CRITICAL][/] MT5 connected but failed to retrieve account info!")
            return False

        # Load or setup persistent account configuration
        self.acc_cfg = self.acc_mgr.get_or_setup_account(acc)
        if self.acc_cfg.get("target_locked", False):
            self.challenge_target_reached = True

        # Sync active risk percentage to RiskGuardian
        if self.acc_cfg.get("use_custom_phase_risk", False):
            self.risk_guardian.normal_risk_pct = self.acc_cfg.get("risk_per_trade", 0.010)

        # Sync Circuit Breakers from persistent account rules
        if "daily_cb_pct" in self.acc_cfg:
            cb_val = float(self.acc_cfg["daily_cb_pct"])
            self.risk_guardian.daily_cb_pct = cb_val / 100.0 if cb_val > 0.50 else cb_val
        if "max_total_dd_pct" in self.acc_cfg:
            floor_val = float(self.acc_cfg["max_total_dd_pct"])
            self.risk_guardian.hard_floor_pct = floor_val / 100.0 if floor_val > 0.50 else floor_val

        # Sync active notification credentials to TradeNotifier
        if "notifications" in self.acc_cfg:
            self.notifier.update_config(self.acc_cfg["notifications"])

        now_utc = datetime.now(timezone.utc)
        self.risk_guardian.reset_daily_if_new_day(now_utc, acc.balance)
        return True

    def show_interactive_menu(self) -> bool:
        """
        Presents the rich institutional control station and action palette.
        Allows the operator to choose execution mode, view calendar, check spreads,
        manage lifecycle & targets, or verify notifications before commencing trades.
        """
        while True:
            acc = mt5.account_info()
            term = mt5.terminal_info()
            if not acc or not term:
                console.print("[bold red][ERROR][/] Lost connection to MetaTrader 5 account.")
                return False

            now_utc = datetime.now(timezone.utc)
            self.risk_guardian.reset_daily_if_new_day(now_utc, acc.balance)

            # Update High-Water Mark peak equity
            self.acc_mgr.update_high_water_mark(str(acc.login), acc.equity)
            self.acc_cfg = self.acc_mgr.accounts.get(str(acc.login), self.acc_cfg)

            # Sync risk if custom scaling is on
            if self.acc_cfg.get("use_custom_phase_risk", False):
                self.risk_guardian.normal_risk_pct = self.acc_cfg.get("risk_per_trade", 0.010)
            else:
                self.risk_guardian.normal_risk_pct = 0.010

            is_reached, target_info = self.acc_mgr.check_target_progress(str(acc.login), acc.balance)

            daily_loss = max(0.0, self.risk_guardian.day_start_balance - acc.equity)
            daily_room = max(0.0, (self.risk_guardian.day_start_balance * self.risk_guardian.daily_cb_pct) - daily_loss)
            floor_bal = self.base_capital * (1.0 - self.risk_guardian.max_floor_cb_pct)
            floor_room = max(0.0, acc.equity - floor_bal)

            render_institutional_control_center(
                login=str(acc.login),
                server=str(acc.server),
                broker_company=str(term.company),
                balance=acc.balance,
                equity=acc.equity,
                free_margin=acc.margin_free,
                day_start=self.risk_guardian.day_start_balance,
                daily_cb_pct=self.risk_guardian.daily_cb_pct,
                daily_room=daily_room,
                floor_cb_bal=floor_bal,
                floor_room=floor_room,
                news_events_count=len(self.news_guardian.high_impact_events),
                spread_caps=self.spread_guardian.caps,
                account_cfg=self.acc_cfg,
                target_info=target_info
            )

            try:
                choice = input("\nEnter choice [1-9] (Press Enter for [1]): ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[bold yellow]Session terminated by user.[/]")
                return False

            if choice in ["1", ""]:
                self.dry_run = False
                console.print("\n[bold bright_green]✔ LIVE ORDER EXECUTION MODE SELECTED.[/] Orders will be sent to MT5 broker.\n")
                return True
            elif choice == "2":
                self.dry_run = True
                console.print("\n[bold bright_cyan]✔ DRY-RUN PAPER TRADING MODE SELECTED.[/] Zero capital risk forward simulation.\n")
                return True
            elif choice == "3":
                self.manage_lifecycle_interactively(acc)
            elif choice == "4":
                self.manage_notifications_interactively(acc)
            elif choice == "5":
                self.render_economic_calendar()
                input("\nPress Enter to return to main control menu...")
            elif choice == "6":
                self.render_live_spreads()
                input("\nPress Enter to return to main control menu...")
            elif choice == "7":
                self.send_test_alert()
                input("\nPress Enter to return to main control menu...")
            elif choice == "8":
                try:
                    from tests.test_strategy_logic_diagnostics import run_diagnostics
                except ImportError:
                    from test_strategy_logic_diagnostics import run_diagnostics
                run_diagnostics()
                input("\nPress Enter to return to main control menu...")
            elif choice == "9":
                console.print("\n[bold yellow]Shutting down MT5 connection... Session terminated cleanly. Goodbye![/]")
                self.bridge.disconnect()
                sys.exit(0)
            else:
                console.print("[bold red]✖ Invalid choice. Please enter a number between 1 and 9.[/]")

    def manage_notifications_interactively(self, acc: Any):
        """Interactive console for managing Telegram Bot and Discord Webhook remote alerts."""
        acc_id = str(acc.login)
        while True:
            notif_cfg = self.acc_mgr.get_notification_config(acc_id)
            render_notifications_menu(acc_id, notif_cfg)

            try:
                sub_c = input("\nEnter choice [1-6] (Press Enter for [6] Return): ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if sub_c in ["6", ""]:
                break
            elif sub_c == "1":
                self.acc_mgr.update_notification_settings(acc_id, platform="none")
                self.notifier.update_config({"active_platform": "none"})
                console.print("\n[bold yellow]✔ Remote notifications muted (Disabled).[/]")
                input("\nPress Enter to continue...")
            elif sub_c == "2":
                curr_tok = notif_cfg.get("telegram_bot_token", "")
                curr_chat = notif_cfg.get("telegram_chat_id", "")
                tok_prompt = f"Enter Telegram Bot Token [Current: {curr_tok[:10]}...]: " if curr_tok else "Enter Telegram Bot Token: "
                new_tok = input(tok_prompt).strip() or curr_tok
                chat_prompt = f"Enter Telegram Chat ID [Current: {curr_chat}]: " if curr_chat else "Enter Telegram Chat ID: "
                new_chat = input(chat_prompt).strip() or curr_chat

                if new_tok and new_chat:
                    test_ans = input("Send live test message to Telegram right now? [Y/n]: ").strip().lower()
                    if test_ans != "n":
                        console.print("Dispatching test alert to Telegram API...")
                        ok, msg = TradeNotifier.test_connection("telegram", new_tok, new_chat)
                        if ok:
                            console.print(f"\n[bold bright_green]✔ [SUCCESS][/] {msg}")
                        else:
                            console.print(f"\n[bold red]✖ [ERROR][/] Telegram Test Failed: {msg}")

                    plat = "all" if notif_cfg.get("discord_webhook_url") else "telegram"
                    self.acc_mgr.update_notification_settings(acc_id, platform=plat, tg_token=new_tok, tg_chat_id=new_chat)
                    self.notifier.update_config({"active_platform": plat, "telegram_bot_token": new_tok, "telegram_chat_id": new_chat})
                    console.print("[bold bright_green]✔ Telegram settings saved successfully![/]")
                else:
                    console.print("[bold red]✖ Bot Token or Chat ID cannot be empty.[/]")
                input("\nPress Enter to continue...")
            elif sub_c == "3":
                curr_url = notif_cfg.get("discord_webhook_url", "")
                url_prompt = f"Enter Discord Webhook URL [Current: {curr_url[:35]}...]: " if curr_url else "Enter Discord Webhook URL: "
                new_url = input(url_prompt).strip() or curr_url

                if new_url:
                    test_ans = input("Send live test message to Discord right now? [Y/n]: ").strip().lower()
                    if test_ans != "n":
                        console.print("Dispatching test notification to Discord Webhook...")
                        ok, msg = TradeNotifier.test_connection("discord", new_url)
                        if ok:
                            console.print(f"\n[bold bright_green]✔ [SUCCESS][/] {msg}")
                        else:
                            console.print(f"\n[bold red]✖ [ERROR][/] Discord Test Failed: {msg}")

                    plat = "all" if notif_cfg.get("telegram_bot_token") else "discord"
                    self.acc_mgr.update_notification_settings(acc_id, platform=plat, discord_url=new_url)
                    self.notifier.update_config({"active_platform": plat, "discord_webhook_url": new_url})
                    console.print("[bold bright_green]✔ Discord settings saved successfully![/]")
                else:
                    console.print("[bold red]✖ Discord Webhook URL cannot be empty.[/]")
                input("\nPress Enter to continue...")
            elif sub_c == "4":
                self.acc_mgr.update_notification_settings(acc_id, platform="all")
                self.notifier.update_config({"active_platform": "all"})
                console.print("\n[bold bright_green]✔ Dual alerts ENABLED! Notifications will dispatch to both Telegram & Discord.[/]")
                input("\nPress Enter to continue...")
            elif sub_c == "5":
                plat = notif_cfg.get("active_platform", "none").lower()
                if plat == "none":
                    console.print("\n[bold yellow]Notifications are currently muted. Select [2] or [3] to configure first.[/]")
                else:
                    console.print(f"\n[bold cyan]Dispatching live test alert to {plat.upper()}...[/]")
                    self.send_test_alert()
                input("\nPress Enter to continue...")
            else:
                console.print("[bold red]✖ Invalid choice. Please enter a number between 1 and 6.[/]")

    def manage_lifecycle_interactively(self, acc: Any):
        """Interactive submenu for managing prop firm lifecycle phases, targets, and risk scaling."""
        acc_id = str(acc.login)
        while True:
            self.acc_cfg = self.acc_mgr.accounts.get(acc_id, self.acc_cfg)
            fresh_acc = mt5.account_info()
            bal = fresh_acc.balance if fresh_acc else acc.balance
            render_lifecycle_menu(acc_id, self.acc_cfg, bal)

            try:
                sub_c = input("\nEnter choice [1-8] (Press Enter for [8] Return): ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if sub_c in ["8", ""]:
                break
            elif sub_c == "1":
                print("\nSelect New Active Phase:")
                print("  [1] Challenge Phase 1")
                print("  [2] Challenge Phase 2")
                print("  [3] Funded Mode")
                ph_c = input("Enter phase [1-3]: ").strip()
                if ph_c in ["1", "2"]:
                    sb_in = input(f"Enter Starting Balance for Phase {ph_c} [Current Balance: ${bal:,.2f}]: ").strip().lstrip('$').replace(',', '')
                    sb_val = float(sb_in) if sb_in else bal
                    self.acc_mgr.advance_account_phase(acc_id, ph_c, new_start_bal=sb_val, notifier=self.notifier)
                    self.challenge_target_reached = False
                elif ph_c == "3":
                    sb_in = input(f"Enter Starting Balance for Funded Mode [Current Balance: ${bal:,.2f}]: ").strip().lstrip('$').replace(',', '')
                    sb_val = float(sb_in) if sb_in else bal
                    self.acc_mgr.advance_account_phase(acc_id, "funded", new_start_bal=sb_val, notifier=self.notifier)
                    self.challenge_target_reached = False
                self.acc_cfg = self.acc_mgr.accounts[acc_id]
                input("\nPhase updated! Press Enter to continue...")
            elif sub_c == "2":
                cr_in = input(f"Enter new Challenge Risk % [Current: {self.acc_cfg.get('challenge_risk_pct', 1.25):.2f}%]: ").strip().rstrip('%').strip()
                if cr_in:
                    try:
                        self.acc_mgr.update_lifecycle_settings(
                            acc_id,
                            lifecycle=self.acc_cfg.get("account_lifecycle", "challenge"),
                            current_phase=self.acc_cfg.get("current_phase", 1),
                            p1_target=self.acc_cfg.get("phase_1_target_pct", 8.0),
                            p2_target=self.acc_cfg.get("phase_2_target_pct", 5.0),
                            ch_risk=float(cr_in),
                            funded_risk=self.acc_cfg.get("funded_risk_pct", 1.0)
                        )
                        self.acc_cfg = self.acc_mgr.accounts[acc_id]
                        console.print("[bold bright_green]✔ Challenge risk updated successfully![/]")
                    except ValueError:
                        console.print("[bold red]✖ Invalid risk value entered.[/]")
                input("\nPress Enter to continue...")
            elif sub_c == "3":
                fr_in = input(f"Enter new Funded Risk % [Current: {self.acc_cfg.get('funded_risk_pct', 1.00):.2f}%]: ").strip().rstrip('%').strip()
                if fr_in:
                    try:
                        self.acc_mgr.update_lifecycle_settings(
                            acc_id,
                            lifecycle=self.acc_cfg.get("account_lifecycle", "challenge"),
                            current_phase=self.acc_cfg.get("current_phase", 1),
                            p1_target=self.acc_cfg.get("phase_1_target_pct", 8.0),
                            p2_target=self.acc_cfg.get("phase_2_target_pct", 5.0),
                            ch_risk=self.acc_cfg.get("challenge_risk_pct", 1.25),
                            funded_risk=float(fr_in)
                        )
                        self.acc_cfg = self.acc_mgr.accounts[acc_id]
                        console.print("[bold bright_green]✔ Funded risk updated successfully![/]")
                    except ValueError:
                        console.print("[bold red]✖ Invalid risk value entered.[/]")
                input("\nPress Enter to continue...")
            elif sub_c == "4":
                try:
                    curr_tot = self.acc_cfg.get('phase_1_target_pct', 8.0) + self.acc_cfg.get('phase_2_target_pct', 5.0)
                    tot_in = input(f"Enter Overall Target % (Phase 1 + Phase 2 combined) [Current: {curr_tot:.1f}%]: ").strip().rstrip('%').strip()
                    tot_val = float(tot_in) if tot_in else curr_tot
                    p1_in = input(f"Enter Phase 1 Target % [Current: {self.acc_cfg.get('phase_1_target_pct', 8.0):.1f}%]: ").strip().rstrip('%').strip()
                    p1_val = float(p1_in) if p1_in else self.acc_cfg.get("phase_1_target_pct", 8.0)
                    calc_p2 = max(0.0, round(tot_val - p1_val, 2))
                    p2_in = input(f"Enter Phase 2 Target % [Default: {calc_p2:.1f}% based on overall {tot_val:.1f}%]: ").strip().rstrip('%').strip()
                    p2_val = float(p2_in) if p2_in else calc_p2
                    self.acc_mgr.update_lifecycle_settings(
                        acc_id,
                        lifecycle=self.acc_cfg.get("account_lifecycle", "challenge"),
                        current_phase=self.acc_cfg.get("current_phase", 1),
                        p1_target=p1_val,
                        p2_target=p2_val,
                        ch_risk=self.acc_cfg.get("challenge_risk_pct", 1.25),
                        funded_risk=self.acc_cfg.get("funded_risk_pct", 1.0)
                    )
                    self.acc_cfg = self.acc_mgr.accounts[acc_id]
                    console.print("[bold bright_green]✔ Phase targets updated successfully![/]")
                except ValueError:
                    console.print("[bold red]✖ Invalid target percentage entered.[/]")
                input("\nPress Enter to continue...")
            elif sub_c == "5":
                sb_in = input(f"Enter New Starting Balance Anchor [Current: ${self.acc_cfg.get('phase_start_balance', bal):,.2f}]: ").strip().lstrip('$').replace(',', '')
                if sb_in:
                    try:
                        self.acc_mgr.update_lifecycle_settings(
                            acc_id,
                            lifecycle=self.acc_cfg.get("account_lifecycle", "challenge"),
                            current_phase=self.acc_cfg.get("current_phase", 1),
                            p1_target=self.acc_cfg.get("phase_1_target_pct", 8.0),
                            p2_target=self.acc_cfg.get("phase_2_target_pct", 5.0),
                            ch_risk=self.acc_cfg.get("challenge_risk_pct", 1.25),
                            funded_risk=self.acc_cfg.get("funded_risk_pct", 1.0),
                            start_bal=float(sb_in)
                        )
                        self.acc_cfg = self.acc_mgr.accounts[acc_id]
                        console.print("[bold bright_green]✔ Starting balance anchor reset successfully![/]")
                    except ValueError:
                        console.print("[bold red]✖ Invalid starting balance entered.[/]")
                input("\nPress Enter to continue...")
            elif sub_c == "6":
                is_on = self.acc_mgr.toggle_custom_phase_risk(acc_id)
                self.acc_cfg = self.acc_mgr.accounts[acc_id]
                status_str = "ENABLED" if is_on else "DISABLED (Fixed 1.00%)"
                console.print(f"\n[bold bright_green]✔ Phase Risk Scaling is now {status_str}![/]")
                input("\nPress Enter to continue...")
            elif sub_c == "7":
                self.acc_mgr.set_target_locked(acc_id, False)
                self.challenge_target_reached = False
                self.acc_cfg = self.acc_mgr.accounts[acc_id]
                console.print("\n[bold bright_green]✔ Target Lock released! Trading is now active.[/]")
                input("\nPress Enter to continue...")
            else:
                console.print("[bold red]✖ Invalid choice. Please enter a number between 1 and 8.[/]")

    def render_economic_calendar(self):
        """Displays upcoming high-impact events from Forex Factory."""
        events = self.news_guardian.get_upcoming_events(hours_ahead=48)
        render_economic_calendar_table(events, hours_ahead=48)

    def render_live_spreads(self):
        """Fetches live quotes for all 8 assets and compares against Golden Caps."""
        rows = []
        for canon, broker_sym in self.bridge.resolved_symbols.items():
            tick = mt5.symbol_info_tick(broker_sym)
            if not tick or tick.bid <= 0:
                continue
            raw_sp = tick.ask - tick.bid
            mult = self.spread_guardian.get_pip_multiplier(canon)
            spread_units = raw_sp * mult
            cap = self.spread_guardian.caps.get(canon, 5.0)

            unit_str = "$" if "XAU" in canon else ("pt" if "NAS" in canon else "p")
            sp_str = f"{spread_units:.1f} {unit_str}" if unit_str == "p" else (f"${spread_units:.2f}" if unit_str == "$" else f"{spread_units:.1f} pt")
            cap_str = f"{cap:.1f} {unit_str}" if unit_str == "p" else (f"${cap:.2f}" if unit_str == "$" else f"{cap:.1f} pt")

            is_ok = spread_units <= cap
            status_str = "[bold bright_green]ACCEPTABLE[/]" if is_ok else "[bold bright_red]EXCEEDED[/]"

            bid_str = format_symbol_price(canon, tick.bid)
            ask_str = format_symbol_price(canon, tick.ask)

            rows.append({
                "symbol": canon,
                "broker_sym": broker_sym,
                "bid_str": bid_str,
                "ask_str": ask_str,
                "spread_str": sp_str,
                "cap_str": cap_str,
                "status_str": status_str
            })
        render_live_spreads_table(rows)

    def send_test_alert(self):
        """Sends a verification notification via TradeNotifier."""
        console.print("\n[bold cyan]Dispatching remote verification alert...[/]")
        acc = mt5.account_info()
        acc_str = f"{acc.login} ({acc.server})" if acc else "Unknown"
        mode_str = "DRY-RUN PAPER" if self.dry_run else "LIVE MT5"
        test_msg = (
            f"🔔 <b>[REMOTE TEST NOTIFICATION]</b>\n"
            f"Account: <code>{acc_str}</code>\n"
            f"Configured Mode: <b>{mode_str}</b>\n"
            f"Status: 🟢 <b>Connection Verified & Operational!</b>\n"
            f"Dual Engines: DCC ApexHunter v1.2.1 + Lewis Kelly SMC"
        )
        self.notifier.send_message(test_msg)
        console.print("[bold bright_green]✔ Remote verification alert dispatched! Check your Telegram/Discord channel.[/]")

    def initialize(self) -> bool:
        """Initializes connection to MT5 and all strategy dependencies."""
        if not self.bridge.connect():
            console.print("[bold red][CRITICAL][/] Could not connect to MetaTrader 5!")
            return False

        acc = mt5.account_info()
        term = mt5.terminal_info()
        mode_str = "DRY RUN (Paper Mode)" if self.dry_run else "LIVE ORDER EXECUTION"

        if not self.acc_cfg:
            self.acc_cfg = self.acc_mgr.get_or_setup_account(acc)
            if self.acc_cfg.get("target_locked", False):
                self.challenge_target_reached = True
            if self.acc_cfg.get("use_custom_phase_risk", False):
                self.risk_guardian.normal_risk_pct = self.acc_cfg.get("risk_per_trade", 0.010)
            if "daily_cb_pct" in self.acc_cfg:
                cb_val = float(self.acc_cfg["daily_cb_pct"])
                self.risk_guardian.daily_cb_pct = cb_val / 100.0 if cb_val > 0.50 else cb_val
            if "max_total_dd_pct" in self.acc_cfg:
                floor_val = float(self.acc_cfg["max_total_dd_pct"])
                self.risk_guardian.hard_floor_pct = floor_val / 100.0 if floor_val > 0.50 else floor_val

        lifecycle = self.acc_cfg.get("account_lifecycle", "challenge").upper()
        curr_phase = self.acc_cfg.get("current_phase", 1)
        use_scaling = self.acc_cfg.get("use_custom_phase_risk", False)
        active_r = (self.acc_cfg.get("risk_per_trade", 0.01) * 100.0)
        mode_badge = f"CHALLENGE (Phase {curr_phase})" if lifecycle == "CHALLENGE" else "FUNDED (Profit Share)"

        console.print("=" * 85)
        console.print(f"       [bold bright_white]INITIALIZING MASTER INSTITUTIONAL UNIFIED BOT[/] [bright_cyan]({mode_str})[/]")
        console.print("=" * 85)
        console.print(f"  Connected to Broker:     [bold white]{term.company}[/] ({term.name})")
        console.print(f"  Account Login:           [bold white]{acc.login}[/] ([bright_green]{acc.server}[/])")
        console.print(f"  Account Balance:         [bold bright_green]${acc.balance:,.2f}[/] (Equity: ${acc.equity:,.2f})")
        console.print(f"  Execution Mode:          [bold bright_yellow]{mode_str}[/]")
        console.print(f"  Account Lifecycle:       [bold bright_yellow]{mode_badge}[/] (Scaling: {'ON' if use_scaling else 'OFF'}, Risk: {active_r:.2f}%)")
        if lifecycle == "CHALLENGE":
            tgt_pct = self.acc_cfg.get("phase_1_target_pct", 8.0) if str(curr_phase) == "1" else self.acc_cfg.get("phase_2_target_pct", 5.0)
            start_b = self.acc_cfg.get("phase_start_balance", acc.balance)
            console.print(f"  Milestone Target:        +{tgt_pct:.1f}% (${start_b * (1.0 + tgt_pct/100.0):,.2f}) | Start: ${start_b:,.2f}")
        console.print(f"  Simultaneous Engines:    1. ApexHunter v1.2.1 (XAUUSD, NAS100) + 2. Lewis Kelly SMC (6 Pairs)")
        console.print(f"  Market Switcher Status:  [bold bright_green]PERMANENTLY PARALLEL[/] (No Switcher / Filter)")
        console.print(f"  Daily Circuit Breaker:   4.0% (-${acc.balance * 0.040:,.2f}) from 00:00 UTC")
        console.print(f"  Max Floor CB:            8.5% (${self.base_capital * (1.0 - 0.085):,.2f} Floor / 0.20% Recovery Zone)")
        console.print(f"  News Shield:             {self.news_auto_close_min}m Auto-Close / {self.news_embargo_before_min}m Pre-Embargo / 12h Sync")
        console.print(f"  Spread Filter:           Golden Numbers + Max 30% of SL Distance")
        console.print(f"  Max Concurrent Slots:    {self.max_concurrent_trades}")
        console.print("=" * 85)

        # Crash recovery for live trading
        if not self.dry_run:
            live_pos = mt5.positions_get()
            if live_pos:
                rec_count = self.order_manager.recover_from_mt5(live_pos)
                if rec_count > 0:
                    console.print(f"[bold bright_green]  [CRASH RECOVERY] Successfully recovered {rec_count} active position groups from MT5![/]")
                    for grp_id, grp in self.order_manager.active_groups.items():
                        canon = grp['symbol']
                        self.open_positions[canon] = {
                            'group_id': grp_id,
                            'strategy': grp['strategy'],
                            'symbol': canon,
                            'broker_sym': self.bridge.resolved_symbols.get(canon, canon),
                            'direction': 'ACTIVE',
                            'entry_price': grp['entry_price'],
                            'sl': grp['initial_sl'],
                            'be_sl': grp['be_sl'],
                            'splits': [],
                            'tickets': grp['tickets'],
                            'tp1_hit': False,
                            'entry_time': datetime.now(timezone.utc)
                        }

        self.notifier.notify_startup(
            account_id=acc.login,
            server=acc.server,
            mode="live" if not self.dry_run else "dry_run",
            equity=acc.equity,
            balance=acc.balance,
            risk_pct=self.risk_guardian.normal_risk_pct * 100.0,
            daily_dd=self.risk_guardian.daily_cb_pct * 100.0,
            max_dd=self.risk_guardian.max_floor_cb_pct * 100.0,
            symbols=['XAUUSD', 'NAS100', 'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'EURJPY', 'USDCAD'],
            daily_cb=self.risk_guardian.daily_cb_pct * 100.0,
            max_cb=self.risk_guardian.max_floor_cb_pct * 100.0,
            strategy_desc="Simultaneous Dual-Engine: ApexHunter (Gold, Nasdaq) + LK SMC (6 Forex Pairs)"
        )
        self.check_session_and_trap_alerts(datetime.now(timezone.utc), is_startup=True)
        self.logger.info(
            f"MasterUnifiedBot initialized on {term.company} ({acc.server}) - Account: {acc.login}, "
            f"Balance: ${acc.balance:,.2f}, Equity: ${acc.equity:,.2f}, Mode: {mode_str}, "
            f"Lifecycle: {mode_badge}, CB: 4.0% Daily / 8.5% Floor"
        )
        return True

    def get_safe_filling_mode(self, info: Any) -> int:
        """Determines valid filling mode for broker to prevent execution rejection."""
        if not info:
            return mt5.ORDER_FILLING_FOK
        modes = info.filling_mode
        if modes & 1:
            return mt5.ORDER_FILLING_FOK
        elif modes & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def run_cycle(self):
        """Single evaluation cycle."""
        try:
            now_utc = datetime.now(timezone.utc)
            acc = mt5.account_info()
            if not acc:
                return

            current_balance = acc.balance
            current_equity = acc.equity

            # 0. Real-Time Killzone, Dead Trap Hour, & Session Alerts
            self.check_session_and_trap_alerts(now_utc)

            # 1. Update High-Water Mark peak equity & Daily Anchor at 00:00 UTC
            self.acc_mgr.update_high_water_mark(str(acc.login), current_equity)
            if self.risk_guardian.reset_daily_if_new_day(now_utc, current_balance):
                self._cb_notified_today = False
                console.print(f"\n[bold bright_green][DAILY ROLLOVER][/] 00:00 UTC Rollover Anchor Updated: Starting Balance = ${current_balance:,.2f}")
                self.logger.info(f"00:00 UTC Daily Rollover Anchor Updated: Starting Balance = ${current_balance:,.2f}")

            # 1b. End-of-Day Daily Performance Scorecard at True Market Close (21:05 UTC)
            market_close_h = self.get_dynamic_market_close_hour_utc(now_utc)
            if now_utc.hour == market_close_h and now_utc.minute >= 5 and getattr(self, "last_daily_summary_date", None) != now_utc.date():
                self.last_daily_summary_date = now_utc.date()
                self._run_daily_summary(now_utc.date())

            # 2. Check Prop Firm Challenge Milestone Target & Target Lock
            is_reached, target_info = self.acc_mgr.check_target_progress(str(acc.login), current_balance)
            if target_info.get("lifecycle") == "CHALLENGE" and is_reached:
                if not self.challenge_target_reached:
                    self.challenge_target_reached = True
                    self.acc_mgr.set_target_locked(str(acc.login), True)
                    tgt_pct = target_info.get("active_target_pct", 8.0)
                    phase_num = target_info.get("phase", 1)
                    console.print(f"\n🏆 [bold bright_green][EVALUATION TARGET ACHIEVED!][/] Phase {phase_num} profit milestone reached! Balance: ${current_balance:,.2f}")
                    console.print(f"🔒 [bold bright_yellow][TARGET LOCK ENGAGED][/] Trading halted to protect evaluation pass.")
                    self.logger.info(f"[TARGET REACHED] Phase {phase_num} profit milestone reached! Balance: ${current_balance:,.2f} - Trading halted.")
                    self.notifier.notify_challenge_passed(
                        account_id=str(acc.login),
                        phase=phase_num,
                        target_pct=tgt_pct,
                        current_balance=current_balance,
                        profit_dollar=current_balance - self.risk_guardian.baseline_capital,
                        profit_pct=((current_balance - self.risk_guardian.baseline_capital) / self.risk_guardian.baseline_capital) * 100.0,
                        is_final=(phase_num == 2 or str(phase_num).lower() == "funded"),
                        server=acc.server
                    )

            if self.challenge_target_reached:
                # When challenge target is reached, manage existing open positions but do not open new ones
                self.manage_active_positions()
                return

            # 3. Check Emergency Circuit Breakers
            allowed, reason = self.risk_guardian.is_trading_allowed(current_balance, current_equity)
            if not allowed:
                if not getattr(self, "_cb_notified_today", False):
                    self._cb_notified_today = True
                    console.print(f"\n[bold red][CRITICAL HALT][/] {reason}")
                    self.logger.critical(f"Circuit Breaker Triggered: {reason}")
                    cb_type = "floor" if self.risk_guardian.max_floor_cb_breached else "daily"
                    limit_pct = (self.risk_guardian.max_floor_cb_pct * 100.0) if cb_type == "floor" else (self.risk_guardian.daily_cb_pct * 100.0)
                    loss_amt = max(0.0, self.risk_guardian.day_start_balance - current_equity)
                    self.notifier.notify_circuit_breaker(
                        cb_type=cb_type,
                        current_equity=current_equity,
                        limit_pct=limit_pct,
                        loss_amount=loss_amt,
                        hard_limit_pct=self.risk_guardian.hard_floor_pct * 100.0
                    )
                    if self.risk_guardian.hard_floor_breached:
                        self.logger.critical(f"Hard Floor Breached! Emergency closing all positions: {reason}")
                        self.emergency_close_all(reason=f"Hard Floor Breached: {reason}")
                self.manage_active_positions()
                return

            # 4. News Auto-Close Check (10 Minutes Before High-Impact News)
            self.check_news_auto_close(now_utc)

            # 5. Friday Weekend Risk Shield (20:00 UTC Auto-Close)
            self.check_friday_weekend_close(now_utc)

            # 6. Periodic 12-Hour News Calendar Refresh
            self.news_guardian.refresh_calendar(force=False)

            # 7. Manage Active Positions & Automated Breakeven Modifications
            self.manage_active_positions()

            # 8. Scan Assets on Newly Closed Bars (Simultaneous Execution across all assets)
            for canonical, broker_sym in self.bridge.resolved_symbols.items():
                # ApexHunter handles Gold and Nasdaq on M5 closed bar
                if canonical in ['XAUUSD', 'NAS100']:
                    is_closed_5m, bar_5m = self.bridge.check_new_bar_closed(canonical, 'M5')
                    if is_closed_5m:
                        self.evaluate_apexhunter_setup(canonical, broker_sym, bar_5m, now_utc)

                # Lewis Kelly SMC handles Forex Basket on M15 closed bar
                elif canonical in ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'EURJPY', 'USDCAD']:
                    is_closed_15m, bar_15m = self.bridge.check_new_bar_closed(canonical, 'M15')
                    if is_closed_15m:
                        self.evaluate_lewis_kelly_setup(canonical, broker_sym, bar_15m, now_utc)

            # 9. Periodic Live State JSON Export (Every 5 seconds)
            now_t = time.time()
            if now_t - self.last_state_export >= 5.0:
                daily_loss = max(0.0, self.risk_guardian.day_start_balance - current_equity)
                daily_room = max(0.0, (self.risk_guardian.day_start_balance * self.risk_guardian.daily_cb_pct) - daily_loss)
                floor_bal = self.base_capital * (1.0 - self.risk_guardian.max_floor_cb_pct)
                floor_room = max(0.0, current_equity - floor_bal)

                state_dict = {
                    "account_id": str(acc.login),
                    "server": str(acc.server),
                    "balance": current_balance,
                    "equity": current_equity,
                    "margin_free": acc.margin_free,
                    "day_pnl": current_equity - self.risk_guardian.day_start_balance,
                    "daily_cb_room": daily_room,
                    "floor_cb_room": floor_room,
                    "active_slots": len(self.open_positions),
                    "max_slots": self.max_concurrent_trades,
                    "open_positions": self.open_positions,
                    "target_locked": self.challenge_target_reached,
                    "dry_run": self.dry_run
                }
                self.state_exporter.export_state(state_dict)
                self.last_state_export = now_t

        except Exception as e:
            self.logger.error(f"Unhandled exception in run_cycle: {e}", exc_info=True)

    def check_news_auto_close(self, now_utc: datetime):
        """Auto-closes open positions 10m before upcoming high-impact news on affected pairs."""
        if not self.open_positions:
            return

        for sym, pos in list(self.open_positions.items()):
            should_close, event_desc = self.news_guardian.should_close_before_news(
                sym, now_utc, close_lead_min=self.news_auto_close_min
            )
            if should_close:
                console.print(f"\n🚨 [bold bright_yellow][NEWS AUTO-CLOSE ({self.news_auto_close_min}m BEFORE)][/] Liquidating {sym} ahead of {event_desc} to eliminate gap slippage!")
                rel_str = f"{now_utc.strftime('%H:%M')} UTC"
                res_str = f"{(now_utc + timedelta(minutes=self.news_embargo_after_min + self.news_auto_close_min)).strftime('%H:%M')} UTC"
                self.notifier.notify_news_shield_activated(
                    title=event_desc,
                    country=sym[:3],
                    release_time_str=rel_str,
                    resume_time_str=res_str
                )
                self.close_position_by_symbol(sym, reason=f"News Auto-Close: {event_desc}")

    def check_friday_weekend_close(self, now_utc: datetime):
        """Liquidates open positions on Friday at 16:45 NY (Dynamic DST) to protect against weekend gap risk."""
        if self.is_friday_weekend_close_window(now_utc) and self.open_positions:
            console.print(f"\n🛡️ [bold bright_yellow][WEEKEND SHIELD][/] Friday 16:45 NY market close reached. Liquidating all open positions to eliminate weekend gap risk.")
            closed_summary = []
            for sym, pos in list(self.open_positions.items()):
                self.close_position_by_symbol(sym, reason="Friday Weekend Shield (16:45 NY / Dynamic DST)")
                closed_summary.append({
                    "symbol": sym,
                    "ticket": pos.get('tickets', [0])[0] if pos.get('tickets') else 0,
                    "volume": sum(sp.get('volume', 0.0) for sp in pos.get('splits', [])),
                    "pnl": 0.0
                })
            self.notifier.notify_weekend_close(
                closed_trades=closed_summary,
                total_pnl=0.0,
                resume_time_str="Monday 06:00 UTC (11:30 IST) - London Session Open"
            )

    def evaluate_apexhunter_setup(self, canonical: str, broker_sym: str, bar_5m: pd.Series, now_utc: datetime):
        """Evaluates ApexHunter v1.2.1 TripleGuard rules on Gold/Nasdaq newly closed 5M bar."""
        if canonical in self.open_positions:
            return

        bundle = self.bridge.fetch_multi_timeframe_bundle(canonical)
        df_m5 = bundle.get('M5')
        df_h1 = bundle.get('H1')
        if df_m5 is None or df_h1 is None or len(df_m5) < 30 or len(df_h1) < 20:
            return

        df_m5_closed = df_m5.iloc[:-1]
        # Pass full df_h1 so engine.prepare_data applies exact single 1H bar shift without double lag
        engine = self.apex_engines[canonical]
        df_prep = engine.prepare_data(df_m5_closed, df_h1)
        if len(df_prep) < 2:
            return

        prev_bar = df_prep.iloc[-2]
        curr_bar = df_prep.iloc[-1]

        # Forensic Audit Record
        audit_record: Dict[str, Any] = {
            "symbol": canonical,
            "strategy": "APEXHUNTER_1_2_1",
            "timeframe": "M5",
            "open": float(curr_bar.get('open', 0.0)),
            "high": float(curr_bar.get('high', 0.0)),
            "low": float(curr_bar.get('low', 0.0)),
            "close": float(curr_bar.get('close', 0.0)),
            "volume": float(curr_bar.get('volume', 0)),
            "ema9_5m": float(curr_bar.get('ema9_5m', 0.0)),
            "ema20_5m": float(curr_bar.get('ema20_5m', 0.0)),
            "ema_gap": float(curr_bar.get('ema_gap', 0.0)),
            "vwap_5m": float(curr_bar.get('vwap_5m', 0.0)),
            "bias_1h": int(curr_bar.get('bias_1h', 0)),
            "ema9_1h": float(curr_bar.get('ema9_1h', 0.0)),
            "ema20_1h": float(curr_bar.get('ema20_1h', 0.0)),
            "adx_1h": float(curr_bar.get('adx_1h', 0.0)),
            "atr_1h": float(curr_bar.get('atr_1h', 0.0)),
            "h1_ema20_level": float(curr_bar.get('ema20_1h_level', 0.0)),
            "stretch_ratio": float(curr_bar.get('stretch_ratio', 0.0)),
            "chase_ratio": float(curr_bar.get('chase_ratio', 0.0)),
            "sweep_detected": False,
            "sweep_level": 0.0,
            "swept_pts": 0.0,
            "decision": "REJECT_SETUP",
            "reason": "TripleGuard setup rules not met",
            "planned_entry": 0.0,
            "planned_sl": 0.0,
            "planned_tp1": 0.0,
            "planned_tp2": 0.0,
            "planned_lots": 0.0
        }

        sig, reject_reason = engine.evaluate_bar(prev_bar, curr_bar, symbol=canonical, return_reason=True)
        if sig is None:
            audit_record["reason"] = reject_reason
            self.audit_logger.log_candle(audit_record)
            return

        # Liquidity sweep confirmation (Institutional Turtle Soup / Liquidity grab)
        sweep_dir = 1 if sig.signal_type == SignalType.BUY else -1
        has_sweep, sweep_lvl, pts = engine.detect_liquidity_sweep(
            df_m5_closed, len(df_m5_closed) - 1, sweep_dir,
            swing_lookback=24, pullback_window=3
        )
        audit_record["sweep_detected"] = bool(has_sweep)
        audit_record["sweep_level"] = float(sweep_lvl)
        audit_record["swept_pts"] = float(pts)

        if not has_sweep:
            audit_record["decision"] = "REJECT_NO_SWEEP"
            audit_record["reason"] = f"No confirmed 5M liquidity sweep (Dir={sweep_dir}, Lookback=24)"
            self.audit_logger.log_candle(audit_record)
            return

        # Pre-Trade Safety Filters:
        # 1. News Embargo Check (20m before / 15m after)
        is_emb, emb_reason = self.news_guardian.is_news_embargo(
            canonical, now_utc,
            embargo_before_min=self.news_embargo_before_min,
            embargo_after_min=self.news_embargo_after_min
        )
        if is_emb:
            audit_record["decision"] = "BLOCKED_NEWS_EMBARGO"
            audit_record["reason"] = emb_reason
            self.audit_logger.log_candle(audit_record)
            self.logger.warning(f"ApexHunter {canonical} entry blocked by News Embargo: {emb_reason}")
            return

        # 2. Spread Guardian Check
        tick = mt5.symbol_info_tick(broker_sym)
        if not tick or tick.ask <= tick.bid:
            audit_record["decision"] = "BLOCKED_NO_TICK"
            audit_record["reason"] = "MT5 symbol tick quote unavailable"
            self.audit_logger.log_candle(audit_record)
            return

        spread_ok, spread_reason = self.spread_guardian.is_spread_acceptable(
            symbol=canonical,
            live_ask=tick.ask,
            live_bid=tick.bid,
            entry_price=sig.entry_price,
            sl_price=sig.stop_loss
        )
        if not spread_ok:
            audit_record["decision"] = "BLOCKED_SPREAD"
            audit_record["reason"] = spread_reason
            self.audit_logger.log_candle(audit_record)
            self.logger.warning(f"ApexHunter {canonical} entry blocked by Spread Guardian: {spread_reason}")
            return

        # 3. Max Concurrent Trades Limit
        if len(self.open_positions) >= self.max_concurrent_trades:
            audit_record["decision"] = "BLOCKED_MAX_SLOTS"
            audit_record["reason"] = f"Max concurrent positions reached ({len(self.open_positions)}/{self.max_concurrent_trades})"
            self.audit_logger.log_candle(audit_record)
            return

        # 4. Risk Guardian Circuit Breaker & Consecutive Loss Check (ApexHunter)
        acc = mt5.account_info()
        allowed, risk_reason = self.risk_guardian.is_symbol_trading_allowed(canonical, acc.balance, acc.equity)
        if not allowed:
            audit_record["decision"] = "BLOCKED_CIRCUIT_BREAKER"
            audit_record["reason"] = risk_reason
            self.audit_logger.log_candle(audit_record)
            self.logger.warning(f"ApexHunter {canonical} entry blocked by Risk Guardian: {risk_reason}")
            return

        risk_usd = self.risk_guardian.calculate_fitted_risk_usd(acc.balance, acc.equity)
        if risk_usd < 2.0:
            audit_record["decision"] = "BLOCKED_MIN_RISK"
            audit_record["reason"] = f"Calculated risk ${risk_usd:.2f} < $2.00 minimum threshold"
            self.audit_logger.log_candle(audit_record)
            return

        # Log confirmed signal to forensic calculations audit
        audit_record["decision"] = f"SIGNAL_{sig.signal_type.name}"
        audit_record["reason"] = f"TripleGuard entry confirmed at {sig.entry_price:.2f}, SL {sig.stop_loss:.2f}"
        audit_record["planned_entry"] = sig.entry_price
        audit_record["planned_sl"] = sig.stop_loss
        audit_record["planned_tp1"] = sig.take_profit_1
        audit_record["planned_tp2"] = sig.take_profit_2
        self.audit_logger.log_candle(audit_record)
        self.logger.trade(f"[APEXHUNTER] Signal: {canonical} {sig.signal_type.name} @ {sig.entry_price:.2f}, SL: {sig.stop_loss:.2f}, TP1: {sig.take_profit_1:.2f}, TP2: {sig.take_profit_2:.2f}")

        # Execute Split Orders
        self.execute_order(
            strategy='APEXHUNTER_1_2_1',
            symbol=canonical,
            broker_sym=broker_sym,
            signal_type=sig.signal_type,
            entry_price=sig.entry_price,
            stop_loss=sig.stop_loss,
            tp_levels=[sig.take_profit_1, sig.take_profit_2],
            risk_usd=risk_usd
        )

    def evaluate_lewis_kelly_setup(self, canonical: str, broker_sym: str, bar_15m: pd.Series, now_utc: datetime):
        """Evaluates Lewis Kelly SMC setups on Forex pairs."""
        if canonical in self.open_positions:
            return

        # Active during London & NY Sessions (DST aware via London & NY anchors)
        bar_time = bar_15m.name
        bar_ny = bar_time.astimezone(self.tz_ny)
        bar_london = bar_time.astimezone(self.tz_london)
        in_london = (8 <= bar_london.hour < 12)
        in_ny = (8 <= bar_ny.hour < 13)
        session_name = "London" if in_london else ("NY" if in_ny else "Off-Session")

        audit_record: Dict[str, Any] = {
            "symbol": canonical,
            "strategy": "LEWIS_KELLY_SMC",
            "timeframe": "M15",
            "open": float(bar_15m.get('open', 0.0)),
            "high": float(bar_15m.get('high', 0.0)),
            "low": float(bar_15m.get('low', 0.0)),
            "close": float(bar_15m.get('close', 0.0)),
            "volume": float(bar_15m.get('volume', 0)),
            "asian_high": 0.0,
            "asian_low": 0.0,
            "asian_range_pips": 0.0,
            "session_active": session_name,
            "sweep_detected": False,
            "sweep_level": 0.0,
            "swept_pts": 0.0,
            "decision": "REJECT_SETUP",
            "reason": "Outside active trading session",
            "planned_entry": 0.0,
            "planned_sl": 0.0,
            "planned_tp1": 0.0,
            "planned_tp2": 0.0,
            "planned_tp3": 0.0,
            "planned_lots": 0.0
        }

        if not (in_london or in_ny):
            audit_record["decision"] = "REJECT_OUTSIDE_SESSION"
            audit_record["reason"] = f"Outside London & NY killzones (London={bar_london.strftime('%H:%M')}, NY={bar_ny.strftime('%H:%M')})"
            self.audit_logger.log_candle(audit_record)
            return

        if bar_ny.weekday() == 4 and (bar_ny.hour > 16 or (bar_ny.hour == 16 and bar_ny.minute >= 45)):
            audit_record["decision"] = "BLOCKED_FRIDAY_CLOSE"
            audit_record["reason"] = "Friday 16:45 NY weekend close cutoff reached"
            self.audit_logger.log_candle(audit_record)
            return

        bundle = self.bridge.fetch_multi_timeframe_bundle(canonical)
        df_m15 = bundle.get('M15')
        if df_m15 is None or len(df_m15) < 30:
            audit_record["decision"] = "REJECT_INSUFFICIENT_DATA"
            audit_record["reason"] = f"Insufficient M15 bars ({len(df_m15) if df_m15 is not None else 0} < 30)"
            self.audit_logger.log_candle(audit_record)
            return

        df_m15_closed = df_m15.iloc[:-1]

        # Asian Session Extreme Sweep (00:00 - 06:00 UTC / 24 M15 bars)
        today_date = bar_time.date()
        asian_bars = df_m15_closed[(df_m15_closed.index.date == today_date) & (df_m15_closed.index.hour >= 0) & (df_m15_closed.index.hour < 6)]
        if asian_bars.empty or len(asian_bars) < 4:
            audit_record["decision"] = "REJECT_NO_ASIAN_RANGE"
            audit_record["reason"] = f"Asian range not formed yet ({len(asian_bars)} bars found)"
            self.audit_logger.log_candle(audit_record)
            return

        asian_high = float(asian_bars['high'].max())
        asian_low = float(asian_bars['low'].min())
        audit_record["asian_high"] = asian_high
        audit_record["asian_low"] = asian_low

        specs = self.bridge.get_specs(canonical)
        digits = specs.get('digits', 5)
        point = specs.get('point', 0.0001)
        pip_size = 10.0 * point if digits in [3, 5] else point
        audit_record["asian_range_pips"] = round(abs(asian_high - asian_low) / pip_size, 1)

        curr_close = float(bar_15m['close'])
        curr_high = float(bar_15m['high'])
        curr_low = float(bar_15m['low'])

        sig_type = None
        sl_price = 0.0

        if curr_low < asian_low and curr_close > asian_low:
            sig_type = SignalType.BUY
            sl_price = curr_low - (5.0 * pip_size)
            audit_record["sweep_detected"] = True
            audit_record["sweep_level"] = asian_low
            audit_record["swept_pts"] = (asian_low - curr_low) / pip_size
        elif curr_high > asian_high and curr_close < asian_high:
            sig_type = SignalType.SELL
            sl_price = curr_high + (5.0 * pip_size)
            audit_record["sweep_detected"] = True
            audit_record["sweep_level"] = asian_high
            audit_record["swept_pts"] = (curr_high - asian_high) / pip_size

        if sig_type is None:
            audit_record["decision"] = "REJECT_INSIDE_ASIAN_RANGE"
            audit_record["reason"] = f"No Asian extreme sweep: High {curr_high:.5f} <= {asian_high:.5f}, Low {curr_low:.5f} >= {asian_low:.5f}"
            self.audit_logger.log_candle(audit_record)
            return

        sl_dist = abs(curr_close - sl_price)
        if sl_dist <= (2.0 * pip_size):
            audit_record["decision"] = "REJECT_MIN_SL"
            audit_record["reason"] = f"SL distance {sl_dist/pip_size:.1f} pips <= 2.0 pip minimum"
            self.audit_logger.log_candle(audit_record)
            return

        # Target levels: 2.50R, 5.00R, 8.00R
        if sig_type == SignalType.BUY:
            tp1 = curr_close + (2.50 * sl_dist)
            tp2 = curr_close + (5.00 * sl_dist)
            tp3 = curr_close + (8.00 * sl_dist)
        else:
            tp1 = curr_close - (2.50 * sl_dist)
            tp2 = curr_close - (5.00 * sl_dist)
            tp3 = curr_close - (8.00 * sl_dist)

        # Pre-Trade Safety Filters:
        # 1. News Embargo Check
        is_emb, emb_reason = self.news_guardian.is_news_embargo(
            canonical, now_utc,
            embargo_before_min=self.news_embargo_before_min,
            embargo_after_min=self.news_embargo_after_min
        )
        if is_emb:
            audit_record["decision"] = "BLOCKED_NEWS_EMBARGO"
            audit_record["reason"] = emb_reason
            self.audit_logger.log_candle(audit_record)
            self.logger.warning(f"Lewis Kelly {canonical} entry blocked by News Embargo: {emb_reason}")
            return

        # 2. Spread Guardian Check
        tick = mt5.symbol_info_tick(broker_sym)
        if not tick or tick.ask <= tick.bid:
            audit_record["decision"] = "BLOCKED_NO_TICK"
            audit_record["reason"] = "MT5 symbol tick quote unavailable"
            self.audit_logger.log_candle(audit_record)
            return

        spread_ok, spread_reason = self.spread_guardian.is_spread_acceptable(
            symbol=canonical,
            live_ask=tick.ask,
            live_bid=tick.bid,
            entry_price=curr_close,
            sl_price=sl_price
        )
        if not spread_ok:
            audit_record["decision"] = "BLOCKED_SPREAD"
            audit_record["reason"] = spread_reason
            self.audit_logger.log_candle(audit_record)
            self.logger.warning(f"Lewis Kelly {canonical} entry blocked by Spread Guardian: {spread_reason}")
            return

        # 3. Max Concurrent Trades Limit
        if len(self.open_positions) >= self.max_concurrent_trades:
            audit_record["decision"] = "BLOCKED_MAX_SLOTS"
            audit_record["reason"] = f"Max concurrent positions reached ({len(self.open_positions)}/{self.max_concurrent_trades})"
            self.audit_logger.log_candle(audit_record)
            return

        # 4. Risk Guardian Circuit Breaker & Consecutive Loss Check (ApexHunter)
        acc = mt5.account_info()
        allowed, risk_reason = self.risk_guardian.is_symbol_trading_allowed(canonical, acc.balance, acc.equity)
        if not allowed:
            audit_record["decision"] = "BLOCKED_CIRCUIT_BREAKER"
            audit_record["reason"] = risk_reason
            self.audit_logger.log_candle(audit_record)
            self.logger.warning(f"ApexHunter {canonical} entry blocked by Risk Guardian: {risk_reason}")
            return

        risk_usd = self.risk_guardian.calculate_fitted_risk_usd(acc.balance, acc.equity)
        if risk_usd < 2.0:
            audit_record["decision"] = "BLOCKED_MIN_RISK"
            audit_record["reason"] = f"Calculated risk ${risk_usd:.2f} < $2.00 minimum threshold"
            self.audit_logger.log_candle(audit_record)
            return

        audit_record["decision"] = f"SIGNAL_{sig_type.name}"
        audit_record["reason"] = f"Asian Extreme sweep confirmed (Entry {curr_close:.5f}, SL {sl_price:.5f})"
        audit_record["planned_entry"] = curr_close
        audit_record["planned_sl"] = sl_price
        audit_record["planned_tp1"] = tp1
        audit_record["planned_tp2"] = tp2
        audit_record["planned_tp3"] = tp3
        self.audit_logger.log_candle(audit_record)
        self.logger.trade(f"[LEWIS_KELLY_SMC] Signal: {canonical} {sig_type.name} @ {curr_close:.5f}, SL: {sl_price:.5f}, TP1: {tp1:.5f}, TP2: {tp2:.5f}, TP3: {tp3:.5f}")

        self.execute_order(
            strategy='LEWIS_KELLY_SMC',
            symbol=canonical,
            broker_sym=broker_sym,
            signal_type=sig_type,
            entry_price=curr_close,
            stop_loss=sl_price,
            tp_levels=[tp1, tp2, tp3],
            risk_usd=risk_usd
        )

    def execute_order(
        self,
        strategy: str,
        symbol: str,
        broker_sym: str,
        signal_type: SignalType,
        entry_price: float,
        stop_loss: float,
        tp_levels: List[float],
        risk_usd: float
    ):
        """Calculates exact lot size and executes server-side split orders."""
        specs = self.bridge.get_specs(symbol)
        digits = specs.get('digits', 5)
        tick_sz = specs.get('tick_size', 0.0001)
        tick_val = specs.get('tick_value', 1.0)
        vol_min = specs.get('volume_min', 0.01)
        vol_step = specs.get('volume_step', 0.01)
        vol_max = specs.get('volume_max', 100.0)

        # Calculate exact total lots
        total_lots = self.risk_guardian.calculate_lot_size(
            symbol=symbol,
            entry_price=entry_price,
            sl_price=stop_loss,
            tick_size=tick_sz,
            tick_value=tick_val,
            volume_min=vol_min,
            volume_step=vol_step,
            volume_max=vol_max,
            risk_usd=risk_usd
        )

        # Generate split orders
        splits = self.order_manager.calculate_order_splits(
            strategy=strategy,
            total_lots=total_lots,
            tp_levels=tp_levels,
            volume_min=vol_min,
            volume_step=vol_step
        )

        dir_str = "BUY" if signal_type == SignalType.BUY else "SELL"
        order_type = mt5.ORDER_TYPE_BUY if signal_type == SignalType.BUY else mt5.ORDER_TYPE_SELL

        console.print(f"\n[bold bright_green][EXECUTION][/] {strategy} on {symbol}: [bold bright_cyan]{dir_str}[/] Total Lots: {total_lots:.2f} ({len(splits)} Splits)")

        raw_tick = mt5.symbol_info_tick(broker_sym)
        live_entry_p = (raw_tick.ask if signal_type == SignalType.BUY else raw_tick.bid) if raw_tick else entry_price
        sl_dist = abs(entry_price - stop_loss)
        be_sl = entry_price + (0.2 * sl_dist) if signal_type == SignalType.BUY else entry_price - (0.2 * sl_dist)

        group_id = f"{symbol}_{int(time.time())}"
        tickets = []

        if self.dry_run:
            for i, sp in enumerate(splits):
                ticket = 999000 + len(self.open_positions) * 3 + (i + 1)
                tickets.append(ticket)
                console.print(f"  [DRY-RUN FILL] Order {i+1} ({sp['label']}): #{ticket} ({sp['volume']} lots) | TP: {sp['tp']:.{digits}f}")

            self.open_positions[symbol] = {
                'group_id': group_id,
                'strategy': strategy,
                'symbol': symbol,
                'broker_sym': broker_sym,
                'direction': dir_str,
                'entry_price': entry_price,
                'sl': stop_loss,
                'be_sl': be_sl,
                'splits': splits,
                'active_splits': splits[1:],
                'tickets': tickets,
                'tp1_hit': False,
                'entry_time': datetime.now(timezone.utc)
            }
            self.order_manager.register_position_group(group_id, strategy, symbol, entry_price, stop_loss, be_sl, tickets)
            q = self.bridge.get_latest_quote(symbol)
            self.notifier.notify_trade_opened(
                symbol=symbol,
                direction=dir_str,
                entry_price=entry_price,
                spread=q['spread'] if q else 0.0,
                total_lots=total_lots,
                sl=stop_loss,
                splits=splits,
                tickets=tickets,
                strategy=strategy,
                is_dry_run=True
            )
            return

        # Real Live Order Send to MT5
        info = mt5.symbol_info(broker_sym)
        fill_mode = self.get_safe_filling_mode(info)

        for i, sp in enumerate(splits):
            magic = self.magic_base + (i + 1)
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": broker_sym,
                "volume": sp['volume'],
                "type": order_type,
                "price": live_entry_p,
                "sl": round(stop_loss, digits),
                "tp": round(sp['tp'], digits),
                "deviation": 20,
                "magic": magic,
                "comment": f"{strategy[:3]}_T{i+1}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": fill_mode,
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                tickets.append(res.order)
                console.print(f"  [bold bright_green][MT5 FILLED][/] Order {i+1} ({sp['label']}): Ticket #{res.order} ({sp['volume']} lots)")
            else:
                err = res.comment if res else "No response"
                console.print(f"  [bold red][MT5 ERROR][/] Order {i+1} failed: {err}")

        if tickets:
            self.open_positions[symbol] = {
                'group_id': group_id,
                'strategy': strategy,
                'symbol': symbol,
                'broker_sym': broker_sym,
                'direction': dir_str,
                'entry_price': live_entry_p,
                'sl': stop_loss,
                'be_sl': be_sl,
                'splits': splits,
                'tickets': tickets,
                'tp1_hit': False,
                'entry_time': datetime.now(timezone.utc)
            }
            self.order_manager.register_position_group(group_id, strategy, symbol, live_entry_p, stop_loss, be_sl, tickets)
            q = self.bridge.get_latest_quote(symbol)
            self.notifier.notify_trade_opened(
                symbol=symbol,
                direction=dir_str,
                entry_price=live_entry_p,
                spread=q['spread'] if q else 0.0,
                total_lots=total_lots,
                sl=stop_loss,
                splits=splits,
                tickets=tickets,
                strategy=strategy,
                is_dry_run=False
            )

    def manage_active_positions(self):
        """Monitors live positions and shifts sibling tickets to Breakeven upon TP1 hit."""
        if not self.open_positions:
            return

        if self.dry_run:
            # Full realistic exit simulation in dry-run paper mode
            for sym in list(self.open_positions.keys()):
                pos = self.open_positions[sym]
                q = self.bridge.get_latest_quote(sym)
                if not q:
                    continue
                current_p = q['bid'] if pos['direction'] == 'BUY' else q['ask']
                is_buy = (pos['direction'] == 'BUY')

                # 1. Check Stop Loss Hit
                sl_hit = (current_p <= pos['sl']) if is_buy else (current_p >= pos['sl'])
                if sl_hit:
                    acc_info = mt5.account_info()
                    bal = acc_info.balance if acc_info else self.risk_guardian.day_start_balance
                    daily_loss = max(0.0, self.risk_guardian.day_start_balance - (acc_info.equity if acc_info else bal))
                    rem_cushion = max(0.0, (self.risk_guardian.day_start_balance * self.risk_guardian.daily_cb_pct) - daily_loss)

                    if pos['tp1_hit']:
                        self.risk_guardian.record_trade_result(sym, is_loss=False)
                        pnl_est = self.base_capital * self.risk_guardian.normal_risk_pct * 0.4
                        console.print(f"\n[bold bright_yellow][DRY-RUN EXIT][/] {sym} Sibling orders exited at Breakeven (+0.2R) at {current_p:.5f}.")
                        self.notifier.notify_trade_closed(
                            symbol=sym,
                            ticket=pos['tickets'][-1] if pos['tickets'] else 0,
                            exit_reason="Breakeven Buffer Hit (+0.2R)",
                            pnl=pnl_est,
                            day_pnl=bal - self.risk_guardian.day_start_balance,
                            remaining_cushion=rem_cushion,
                            daily_cb_pct=self.risk_guardian.daily_cb_pct * 100.0,
                            strategy=pos.get('strategy', 'ApexHunter')
                        )
                    else:
                        self.risk_guardian.record_trade_result(sym, is_loss=True)
                        pnl_loss = -self.base_capital * self.risk_guardian.normal_risk_pct
                        console.print(f"\n[bold red][DRY-RUN EXIT][/] {sym} Trade hit Stop Loss at {current_p:.5f}.")
                        self.notifier.notify_trade_closed(
                            symbol=sym,
                            ticket=pos['tickets'][0] if pos['tickets'] else 0,
                            exit_reason="Stop Loss Hit",
                            pnl=pnl_loss,
                            day_pnl=bal - self.risk_guardian.day_start_balance,
                            remaining_cushion=rem_cushion,
                            daily_cb_pct=self.risk_guardian.daily_cb_pct * 100.0,
                            strategy=pos.get('strategy', 'ApexHunter')
                        )
                    del self.open_positions[sym]
                    continue

                # 2. Check TP1 Hit
                if not pos['tp1_hit']:
                    tp1_p = pos['splits'][0]['tp']
                    tp1_reached = (current_p >= tp1_p) if is_buy else (current_p <= tp1_p)
                    if tp1_reached:
                        pos['tp1_hit'] = True
                        pos['sl'] = pos['be_sl'] # Move active SL to Breakeven
                        pos['active_splits'] = pos['splits'][1:]
                        sibling_tix = pos['tickets'][1:] if len(pos['tickets']) > 1 else pos['tickets']
                        console.print(f"\n[bold bright_green][TARGET HIT][/] {sym} Order 1 hit TP1! Sibling orders moved to Breakeven ({pos['be_sl']:.5f}).")
                        self.notifier.notify_tp1_breakeven(
                            symbol=sym,
                            ticket_a=pos['tickets'][0],
                            ticket_b=sibling_tix,
                            be_sl=pos['be_sl'],
                            strategy=pos.get('strategy', 'ApexHunter')
                        )

                # 3. Check Higher TPs (TP2, TP3)
                if pos['tp1_hit'] and pos.get('active_splits'):
                    remaining_splits = []
                    for sp in pos['active_splits']:
                        tp_reached = (current_p >= sp['tp']) if is_buy else (current_p <= sp['tp'])
                        if tp_reached:
                            console.print(f"\n[bold bright_green][TARGET HIT][/] {sym} Order ({sp['label']}) hit Take Profit at {sp['tp']:.5f}!")
                        else:
                            remaining_splits.append(sp)
                    pos['active_splits'] = remaining_splits
                    if not remaining_splits:
                        self.risk_guardian.record_trade_result(sym, is_loss=False)
                        console.print(f"[bold bright_green][TRADE COMPLETE][/] All Take Profit targets reached for {sym}!")
                        acc_info = mt5.account_info()
                        bal = acc_info.balance if acc_info else self.risk_guardian.day_start_balance
                        daily_loss = max(0.0, self.risk_guardian.day_start_balance - (acc_info.equity if acc_info else bal))
                        rem_cushion = max(0.0, (self.risk_guardian.day_start_balance * self.risk_guardian.daily_cb_pct) - daily_loss)
                        pnl_win = self.base_capital * self.risk_guardian.normal_risk_pct * 1.8
                        self.notifier.notify_trade_closed(
                            symbol=sym,
                            ticket=pos['tickets'][-1] if pos['tickets'] else 0,
                            exit_reason="Full Take Profit Targets Banked",
                            pnl=pnl_win,
                            day_pnl=bal - self.risk_guardian.day_start_balance,
                            remaining_cushion=rem_cushion,
                            daily_cb_pct=self.risk_guardian.daily_cb_pct * 100.0,
                            strategy=pos.get('strategy', 'ApexHunter')
                        )
                        del self.open_positions[sym]
            return

        # Real Live MT5 Position Management
        live_positions = mt5.positions_get()
        live_tickets = [p.ticket for p in live_positions] if live_positions else []
        
        # Check deals history for TP1 fills
        deals = mt5.history_deals_get(datetime.now(timezone.utc) - timedelta(hours=24), datetime.now(timezone.utc))
        closed_deal_tickets = [d.order for d in deals] if deals else []

        mods = self.order_manager.evaluate_breakeven_modifications(live_tickets, closed_deal_tickets)
        for m in mods:
            ticket = m['ticket']
            broker_sym = self.bridge.resolved_symbols.get(m['symbol'], m['symbol'])
            new_sl = m['new_sl']
            success = self.modify_position_sl(ticket, broker_sym, new_sl)
            if success:
                self.order_manager.record_ticket_modified(m['group_id'], ticket)
                self.notifier.notify_tp1_breakeven(
                    symbol=m['symbol'],
                    ticket_a="Deal_History_TP1",
                    ticket_b=ticket,
                    be_sl=new_sl,
                    strategy="Live MT5"
                )

        # Clean up closed groups
        for sym, pos in list(self.open_positions.items()):
            active_tix = [t for t in pos['tickets'] if t in live_tickets]
            if not active_tix:
                console.print(f"[bold yellow][TRADE CLOSED][/] All split orders for {sym} have exited.")
                total_pnl = 0.0
                closed_summary = []
                if deals:
                    closed_deals = [d for d in deals if d.order in pos['tickets']]
                    total_pnl = sum(d.profit + d.commission + d.swap for d in closed_deals)
                    for cd in closed_deals:
                        closed_summary.append({
                            "label": cd.comment or "Order",
                            "ticket": cd.ticket,
                            "pnl": cd.profit + cd.commission + cd.swap
                        })
                    is_loss = (total_pnl < 0.0)
                    self.risk_guardian.record_trade_result(sym, is_loss=is_loss)
                    if is_loss:
                        self.logger.warning(f"[RISK GUARDIAN] {sym} trade concluded with loss (${total_pnl:.2f}). Consecutive daily losses: {self.risk_guardian.symbol_consecutive_losses.get(sym, 0)}")
                    else:
                        self.logger.info(f"[RISK GUARDIAN] {sym} trade concluded with profit/BE (${total_pnl:.2f}). Consecutive loss counter reset.")

                acc_info = mt5.account_info()
                bal = acc_info.balance if acc_info else self.risk_guardian.day_start_balance
                daily_loss = max(0.0, self.risk_guardian.day_start_balance - (acc_info.equity if acc_info else bal))
                rem_cushion = max(0.0, (self.risk_guardian.day_start_balance * self.risk_guardian.daily_cb_pct) - daily_loss)

                self.notifier.notify_trade_closed(
                    symbol=sym,
                    ticket=pos.get('tickets', [0])[0] if pos.get('tickets') else 0,
                    exit_reason="All Split Orders Closed",
                    pnl=total_pnl,
                    day_pnl=bal - self.risk_guardian.day_start_balance,
                    daily_dd_pct=(daily_loss / self.risk_guardian.day_start_balance * 100.0) if self.risk_guardian.day_start_balance > 0 else 0.0,
                    remaining_cushion=rem_cushion,
                    daily_cb_pct=self.risk_guardian.daily_cb_pct * 100.0,
                    order_breakdown=closed_summary if closed_summary else None,
                    strategy=pos.get('strategy', 'ApexHunter')
                )
                del self.open_positions[sym]

    def modify_position_sl(self, ticket: int, broker_sym: str, new_sl: float) -> bool:
        """Modifies MT5 position Stop Loss while preserving its original Take Profit."""
        import time
        now_ts = time.time()
        # Cooldown check to prevent hammering MT5 on retries
        if not self.order_manager.can_attempt_modification(ticket, now_ts, cooldown_sec=30.0):
            return False

        specs = self.bridge.get_specs(broker_sym)
        digits = specs.get('digits', 5)
        point = specs.get('point', 0.0001)

        pos_list = mt5.positions_get(ticket=ticket)
        if not pos_list:
            return False
        pos = pos_list[0]

        # Verify broker stops level distance before sending modification request
        s_info = mt5.symbol_info(broker_sym)
        stops_level = s_info.stops_level if s_info else 10
        tick = mt5.symbol_info_tick(broker_sym)
        price_curr = (tick.ask if pos.type == mt5.ORDER_TYPE_SELL else tick.bid) if tick else pos.price_current

        is_valid_distance = OrderManager.check_price_stops_distance(
            order_type=pos.type,
            current_price=price_curr,
            target_sl=new_sl,
            stops_level_pts=stops_level,
            point=point
        )
        if not is_valid_distance:
            self.order_manager.record_modification_attempt(ticket, now_ts)
            self.logger.info(
                f"[SL SHIFT PENDING] Ticket #{ticket} on {broker_sym}: Market price ({price_curr:.{digits}f}) "
                f"too close to target Breakeven SL ({new_sl:.{digits}f}) for broker stops_level ({stops_level} pts). "
                f"Waiting for price advance (cooling down 30s)..."
            )
            return False

        self.order_manager.record_modification_attempt(ticket, now_ts)
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": broker_sym,
            "sl": round(new_sl, digits),
            "tp": round(pos.tp, digits)
        }
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            console.print(f"  [bold bright_green][MT5 SUCCESS][/] Ticket #{ticket} Stop Loss shifted to {new_sl:.{digits}f}")
            self.logger.trade(f"[SL SHIFT SUCCESS] Ticket #{ticket} on {broker_sym} SL modified to {new_sl:.{digits}f}")
            return True
        else:
            err = res.comment if res else "No response"
            console.print(f"  [bold red][MT5 WARN][/] Failed to shift Ticket #{ticket} SL: {err}")
            self.logger.warning(f"[SL SHIFT FAILED] Ticket #{ticket} on {broker_sym}: {err}")
            return False

    def close_position_by_symbol(self, symbol: str, reason: str = "Manual/News Close"):
        """Liquidates all positions for a specific symbol."""
        self.logger.trade(f"[LIQUIDATE POSITION] Liquidating all positions on {symbol} - Reason: {reason}")
        if symbol in self.open_positions:
            del self.open_positions[symbol]

        if not self.dry_run:
            positions = mt5.positions_get()
            if positions:
                broker_sym = self.bridge.resolved_symbols.get(symbol, symbol)
                for p in positions:
                    if p.symbol == broker_sym or symbol in p.symbol:
                        order_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                        tick = mt5.symbol_info_tick(p.symbol)
                        price = (tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask) if tick else p.price_current
                        req = {
                            "action": mt5.TRADE_ACTION_DEAL,
                            "position": p.ticket,
                            "symbol": p.symbol,
                            "volume": p.volume,
                            "type": order_type,
                            "price": price,
                            "deviation": 30,
                        }
                        res = mt5.order_send(req)
                        self.logger.trade(f"[MT5 CLOSE] Ticket #{p.ticket} on {p.symbol} closed at {price} (RetCode={res.retcode if res else 'None'})")

    def emergency_close_all(self, reason: str = "Emergency Circuit Breaker Close"):
        """Emergency closes all open positions across all symbols immediately."""
        self.logger.critical(f"[EMERGENCY CLOSE ALL] Triggered: {reason}")
        for sym in list(self.open_positions.keys()):
            self.close_position_by_symbol(sym, reason=reason)

    def display_live_dashboard(self, acc: Any, now_utc: datetime, target_info: Optional[Dict[str, Any]] = None):
        """Renders institutional live status telemetry to terminal using Rich."""
        if target_info is None:
            _, target_info = self.acc_mgr.check_target_progress(str(acc.login), acc.balance)

        daily_loss = max(0.0, self.risk_guardian.day_start_balance - acc.equity)
        rem_daily_budget = max(0.0, (self.risk_guardian.day_start_balance * self.risk_guardian.daily_cb_pct) - daily_loss)

        asset_quotes = []
        for canon, broker_sym in self.bridge.resolved_symbols.items():
            tick = mt5.symbol_info_tick(broker_sym)
            strat = "APEXHUNTER 1.2.1" if canon in ['XAUUSD', 'NAS100'] else "LEWIS KELLY SMC"

            if canon in self.open_positions:
                pos = self.open_positions[canon]
                state_str = f"[bold bright_cyan]ACTIVE ({pos['direction']})[/]"
            else:
                is_emb, _ = self.news_guardian.is_news_embargo(
                    canon, now_utc,
                    embargo_before_min=self.news_embargo_before_min,
                    embargo_after_min=self.news_embargo_after_min
                )
                if is_emb:
                    state_str = "[bold bright_red]EMBARGO[/]"
                else:
                    state_str = "[bold green]HUNTING[/]"

            if tick and tick.bid > 0:
                raw_sp = tick.ask - tick.bid
                mult = self.spread_guardian.get_pip_multiplier(canon)
                spread_units = raw_sp * mult
                cap = self.spread_guardian.caps.get(canon, 5.0)

                unit_str = "$" if "XAU" in canon else ("pt" if "NAS" in canon else "p")
                sp_str = f"{spread_units:.1f} {unit_str}" if unit_str == "p" else (f"${spread_units:.2f}" if unit_str == "$" else f"{spread_units:.1f} pt")
                cap_str = f"{cap:.1f} {unit_str}" if unit_str == "p" else (f"${cap:.2f}" if unit_str == "$" else f"{cap:.1f} pt")

                bid_str = format_symbol_price(canon, tick.bid)
                ask_str = format_symbol_price(canon, tick.ask)
            else:
                bid_str, ask_str, sp_str, cap_str = "-", "-", "-", "-"

            asset_quotes.append({
                "symbol": canon,
                "bid": bid_str,
                "ask": ask_str,
                "spread": sp_str,
                "cap": cap_str,
                "strategy": strat,
                "state": state_str
            })

        render_telemetry_dashboard(
            now_utc=now_utc,
            login=str(acc.login),
            server=str(acc.server),
            balance=acc.balance,
            equity=acc.equity,
            day_start=self.risk_guardian.day_start_balance,
            daily_room=rem_daily_budget,
            open_positions=self.open_positions,
            max_slots=self.max_concurrent_trades,
            news_events_count=len(self.news_guardian.high_impact_events),
            asset_quotes=asset_quotes,
            target_info=target_info
        )

    def run(self):
        """Main bot execution daemon with dynamic in-place surveillance display."""
        if not self.initialize():
            return

        mode_badge = "[bold bright_yellow]PAPER SIMULATION[/]" if self.dry_run else "[bold bright_green]LIVE MT5 EXECUTION[/]"
        console.print(f"\n[bold bright_white][BOT ACTIVE][/] Streaming live market data in {mode_badge}. Press [bold cyan]Ctrl+C[/] to stop.\n")

        self.live = Live(console=console, refresh_per_second=4, transient=False)
        self.live.start()

        try:
            last_cycle_time = 0.0
            while True:
                now_time = time.time()
                now_utc = datetime.now(timezone.utc)

                # 1. Run trading logic cycle every 1.0 second
                if now_time - last_cycle_time >= 1.0:
                    self.run_cycle()
                    last_cycle_time = now_time

                # 2. Update real-time surveillance line in-place at 4 FPS (0.25s interval)
                acc = mt5.account_info()
                status_line = self.build_surveillance_status(now_utc, acc)
                self.live.update(status_line)
                time.sleep(0.25)

        except KeyboardInterrupt:
            console.print("\n[bold yellow][SHUTDOWN] Bot stopped by user (Ctrl+C). Disconnecting MT5...[/]")
            self.logger.info("Bot execution terminated by user (Ctrl+C).")
            self.notifier.notify_shutdown(reason="User Stopped (Ctrl+C)")
        except Exception as e:
            self.logger.critical(f"Critical fatal exception in main run loop: {e}", exc_info=True)
            console.print(f"\n[bold red][CRITICAL ERROR][/] {e}")
            self.notifier.notify_shutdown(reason=f"Critical Error: {e}")
        finally:
            if self.live is not None:
                self.live.stop()
            self.bridge.disconnect()
            self.logger.info("MT5 connection closed cleanly. Bot session concluded.")
            console.print("[bold bright_green][SHUTDOWN] MT5 Connection Closed cleanly. Session concluded.[/]")

# Backward compatibility alias
MasterRegimeBot = MasterUnifiedBot

def main():
    parser = argparse.ArgumentParser(description="Master Institutional Unified Bot (Simultaneous Dual-Engine Execution)")
    parser.add_argument("--live", action="store_true", help="Start in Live MT5 Order Execution Mode")
    parser.add_argument("--dry-run", action="store_true", help="Start in Dry-Run Paper Trading Mode")
    parser.add_argument("--auto", "-y", action="store_true", help="Bypass interactive control menu and run immediately")
    parser.add_argument("--capital", type=float, default=None, help="Base account capital override")
    parser.add_argument("--test-strategy", action="store_true", help="Run 100% strategy logic and indicator calculation diagnostic test suite")
    args = parser.parse_args()

    if args.test_strategy:
        try:
            from tests.test_strategy_logic_diagnostics import run_diagnostics
        except ImportError:
            from test_strategy_logic_diagnostics import run_diagnostics
        ok = run_diagnostics()
        sys.exit(0 if ok else 1)

    is_dry_run = True
    if args.live:
        is_dry_run = False
    elif args.dry_run:
        is_dry_run = True

    bot = MasterUnifiedBot(dry_run=is_dry_run, base_capital=args.capital)

    if not bot.connect_and_check():
        sys.exit(1)

    if not args.auto:
        should_run = bot.show_interactive_menu()
        if not should_run:
            sys.exit(0)

    bot.run()

if __name__ == '__main__':
    main()
