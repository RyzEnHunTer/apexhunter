"""
Institutional Remote Notification Engine
========================================
Location: live/notifier.py

Provides full parity with DCC ApexHunter institutional notification engine.
Supports dual-engine remote alerts:
1. Telegram Bot (Bot Token & Chat ID)
2. Discord Webhook (Webhook URL & Rich Embeds)

Key Architecture & Guarantees:
- Non-blocking: Dispatches all HTTP requests asynchronously in a daemon queue worker thread
  so market tick execution in MT5 is NEVER delayed by internet latency.
- Supported Platforms: "all" (both), "telegram", "discord", or "none" (muted).
- Rich Embeds: Formatted Telegram HTML + Discord rich embeds with dynamic status colors.
- Dual Timestamps: Every single alert carries dual UTC + IST timestamps.
- Zero External Dependencies: Built strictly with standard library urllib.request.
"""

from __future__ import annotations

import os
import json
import ssl
import time
import queue
import threading
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

TZ_IST = timezone(timedelta(hours=5, minutes=30))


def _get_ssl_context() -> ssl.SSLContext:
    """Returns an SSL context prioritizing certifi CA bundle with unverified fallback."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def format_dual_time(dt: Optional[datetime] = None, include_date: bool = True) -> str:
    """Formats datetime into dual UTC + IST string for clear local time visibility."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    dt_ist = dt.astimezone(TZ_IST)
    if include_date:
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} UTC ({dt_ist.strftime('%Y-%m-%d %H:%M:%S')} IST)"
    return f"{dt.strftime('%H:%M:%S')} UTC ({dt_ist.strftime('%H:%M:%S')} IST)"


def format_dual_hhmm(utc_hhmm: str) -> str:
    """Converts a UTC HH:MM string to dual 'HH:MM UTC (HH:MM IST)' string."""
    if "IST" in utc_hhmm:
        return utc_hhmm
    try:
        raw = utc_hhmm.replace("UTC", "").strip()
        parts = raw.split(":")
        h, m = int(parts[0]), int(parts[1][:2])
        now_d = datetime.now(timezone.utc).date()
        dt_utc = datetime(now_d.year, now_d.month, now_d.day, h, m, tzinfo=timezone.utc)
        dt_ist = dt_utc.astimezone(TZ_IST)
        return f"{h:02d}:{m:02d} UTC ({dt_ist.strftime('%H:%M')} IST)"
    except Exception:
        return f"{utc_hhmm}"


def format_dollar(val: float, force_sign: bool = True) -> str:
    """Formats dollar value cleanly with proper +$ or -$ placement."""
    if val >= 0:
        return f"+${val:,.2f}" if force_sign else f"${val:,.2f}"
    return f"-${abs(val):,.2f}"


class TradeNotifier:
    """Manages remote alerts for Telegram and Discord in a non-blocking background queue."""

    def __init__(self, config_or_path: Optional[Any] = None, config_path: Optional[Any] = None):
        self.active_platform: str = "all"
        self.discord_url: str = ""
        self.tg_token: str = ""
        self.tg_chat_id: str = ""

        cfg_target = config_or_path if config_or_path is not None else config_path
        if isinstance(cfg_target, dict):
            self.update_config(cfg_target)
        else:
            self._load_from_path(cfg_target)

        # Non-blocking Queue + Daemon Worker Thread
        self.msg_queue: queue.Queue = queue.Queue(maxsize=100)
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="NotifierWorker")
        self.worker_thread.start()

    def _load_from_path(self, config_path: Optional[str] = None):
        if config_path is None or not os.path.exists(config_path):
            current_dir = os.path.dirname(os.path.abspath(__file__))
            candidate1 = os.path.join(current_dir, "config.json")
            candidate2 = os.path.join(os.path.dirname(current_dir), "live", "config.json")
            if os.path.exists(candidate1):
                config_path = candidate1
            elif os.path.exists(candidate2):
                config_path = candidate2
            else:
                config_path = "config.json"

        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    notif = data.get("notifications", {})
                    self.update_config(notif)
            except Exception as e:
                print(f"[TradeNotifier WARN] Failed to load config from {config_path}: {e}")

    def update_config(self, notif_cfg: Dict[str, Any]):
        """Dynamically updates active credentials and platform filter."""
        if "notifications" in notif_cfg and isinstance(notif_cfg["notifications"], dict):
            notif_cfg = notif_cfg["notifications"]

        self.active_platform = str(notif_cfg.get("active_platform", self.active_platform)).lower()
        self.discord_url = str(notif_cfg.get("discord_webhook_url", self.discord_url)).strip()
        self.tg_token = str(notif_cfg.get("telegram_bot_token", self.tg_token)).strip()
        self.tg_chat_id = str(notif_cfg.get("telegram_chat_id", self.tg_chat_id)).strip()

    def _worker_loop(self):
        """Background worker thread draining the queue and dispatching HTTP requests."""
        while True:
            try:
                item = self.msg_queue.get()
                if item is None:
                    break

                try:
                    platform, payload = item
                    if platform == "telegram":
                        token = self.tg_token
                        chat_id = self.tg_chat_id
                        if token and chat_id:
                            self._send_telegram_http(token, chat_id, payload.get("text", ""))
                    elif platform == "discord":
                        webhook_url = self.discord_url
                        if webhook_url:
                            self._send_discord_http(
                                webhook_url,
                                payload.get("content", ""),
                                payload.get("embed", None)
                            )
                finally:
                    self.msg_queue.task_done()
            except Exception as e:
                print(f"[TradeNotifier Worker Error] {e}")
                time.sleep(0.5)

    def _enqueue(self, payload: Dict[str, Any]):
        """Enqueues payload for background transmission without blocking caller."""
        plat = self.active_platform
        if plat == "none":
            return

        targets: List[str] = []
        if plat in ("all", "both"):
            targets = ["telegram", "discord"]
        elif plat == "telegram":
            targets = ["telegram"]
        elif plat == "discord":
            targets = ["discord"]

        for target in targets:
            try:
                self.msg_queue.put_nowait((target, payload))
            except queue.Full:
                print("[TradeNotifier] Warning: Alert queue full, dropping alert to protect MT5 tick speed.")

    # -------------------------------------------------------------------------
    # HTTP Senders (Executed in background worker thread)
    # -------------------------------------------------------------------------
    @classmethod
    def _send_telegram_http(cls, token: str, chat_id: str, message: str, timeout: int = 6) -> Tuple[bool, str]:
        if not token or not chat_id:
            return False, "Telegram Bot Token or Chat ID is empty."

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "MasterUnifiedBot/2.0"}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context()) as response:
                if response.status == 200:
                    return True, "Telegram notification sent successfully."
                return False, f"HTTP Status {response.status}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"Telegram HTTPError: {err_msg}"
        except Exception as e:
            err_str = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in err_str or "certificate verify failed" in err_str.lower():
                try:
                    fallback_ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=timeout, context=fallback_ctx) as response:
                        if response.status == 200:
                            return True, "Telegram notification sent successfully (via fallback SSL)."
                        return False, f"HTTP Status {response.status}"
                except Exception as retry_err:
                    return False, f"Telegram SSL Error: {retry_err}"
            return False, f"Telegram Error: {e}"

    @classmethod
    def _send_discord_http(
        cls, webhook_url: str, content: str, embed: Optional[Dict[str, Any]] = None, timeout: int = 6
    ) -> Tuple[bool, str]:
        if not webhook_url:
            return False, "Discord Webhook URL is empty."

        # Convert simple HTML formatting to Discord markdown if content is provided
        discord_content = content
        if discord_content:
            discord_content = discord_content.replace("<b>", "**").replace("</b>", "**")
            discord_content = discord_content.replace("<i>", "*").replace("</i>", "*")
            discord_content = discord_content.replace("<code>", "`").replace("</code>", "`")

        body: Dict[str, Any] = {"content": discord_content or ""}
        if embed:
            body["embeds"] = [embed]

        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "MasterUnifiedBot/2.0"}
        req = urllib.request.Request(webhook_url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context()) as response:
                if response.status in (200, 204):
                    return True, "Discord notification sent successfully."
                return False, f"HTTP Status {response.status}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"Discord HTTPError: {err_msg}"
        except Exception as e:
            err_str = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in err_str or "certificate verify failed" in err_str.lower():
                try:
                    fallback_ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=timeout, context=fallback_ctx) as response:
                        if response.status in (200, 204):
                            return True, "Discord notification sent successfully (via fallback SSL)."
                        return False, f"HTTP Status {response.status}"
                except Exception as retry_err:
                    return False, f"Discord SSL Error: {retry_err}"
            return False, f"Discord Error: {e}"

    # -------------------------------------------------------------------------
    # Public Connection Test
    # -------------------------------------------------------------------------
    @classmethod
    def test_connection(cls, platform: str, token_or_url: str, chat_id: Optional[str] = None) -> Tuple[bool, str]:
        """Synchronous connection test triggered from the interactive menu."""
        test_msg = (
            f"🔔 <b>[MASTER UNIFIED BOT CONNECTION TEST]</b>\n\n"
            f"Your remote notification channel is active and operational!\n"
            f"Time: {format_dual_time(include_date=True)}"
        )
        p = platform.lower()
        if p == "telegram":
            if not token_or_url or not chat_id:
                return False, "Bot Token and Chat ID cannot be empty."
            return cls._send_telegram_http(token_or_url.strip(), chat_id.strip(), test_msg, timeout=8)
        elif p == "discord":
            if not token_or_url:
                return False, "Discord Webhook URL cannot be empty."
            embed = {
                "title": "🔔 MASTER UNIFIED BOT CONNECTION TEST",
                "description": "Your remote Discord notification channel is active and operational!",
                "color": 0x00FFAA,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": f"Tested at {format_dual_time(include_date=True)}"}
            }
            return cls._send_discord_http(token_or_url.strip(), "", embed=embed, timeout=8)
        return False, f"Unknown platform: {platform}"

    # -------------------------------------------------------------------------
    # Generic Send Message (Queue Dispatched)
    # -------------------------------------------------------------------------
    def send_message(self, message: str):
        """Sends a generic HTML message asynchronously to active platform(s)."""
        self._enqueue({"text": message, "content": message, "embed": None})

    def send_test(self) -> bool:
        """Sends an active channel verification test."""
        active = []
        if self.discord_url and self.active_platform in ("all", "both", "discord"):
            active.append("Discord")
        if self.tg_token and self.tg_chat_id and self.active_platform in ("all", "both", "telegram"):
            active.append("Telegram")

        if active:
            msg = f"🟢 <b>Master Unified Bot Active!</b>\nNotifications enabled for: <b>{', '.join(active)}</b>"
            self.send_message(msg)
            return True
        return False

    # -------------------------------------------------------------------------
    # Structured Activity Notification Methods (Parity with DC ApexHunter)
    # -------------------------------------------------------------------------
    def notify_startup(
        self,
        account_id: int,
        server: str,
        mode: str,
        equity: float,
        balance: float,
        risk_pct: float,
        daily_dd: float,
        max_dd: float,
        symbols: List[str],
        daily_cb: Optional[float] = None,
        max_cb: Optional[float] = None,
        strategy_desc: Optional[str] = None
    ):
        """Notifies when the bot initializes and begins live surveillance."""
        mode_tag = "DRY RUN (Paper)" if mode == "dry_run" else "LIVE EXECUTION"
        now_str = format_dual_time(include_date=True)

        d_cb_str = f" | CB: -{daily_cb:.1f}%" if daily_cb is not None else ""
        m_cb_str = f" | CB: -{max_cb:.1f}%" if max_cb is not None else ""
        strat_str = f"\n• <b>Strategy:</b> {strategy_desc}" if strategy_desc else ""

        tg_text = (
            f"🚀 <b>MASTER UNIFIED BOT STARTED [{mode_tag}]</b>\n\n"
            f"• <b>Account:</b> {account_id} ({server})\n"
            f"• <b>Equity:</b> ${equity:,.2f} | <b>Balance:</b> ${balance:,.2f}\n"
            f"• <b>Risk/Trade:</b> {risk_pct:.2f}%\n"
            f"• <b>Daily DD Limit:</b> -{daily_dd:.1f}%{d_cb_str}\n"
            f"• <b>Max Total DD:</b> -{max_dd:.1f}%{m_cb_str}\n"
            f"• <b>Active Assets (8):</b> {', '.join(symbols)}{strat_str}\n"
            f"• <b>Started:</b> {now_str}"
        )

        discord_embed = {
            "title": f"🚀 MASTER UNIFIED BOT STARTED [{mode_tag}]",
            "color": 0x00FF88 if mode == "live" else 0xFFAA00,
            "fields": [
                {"name": "Account", "value": f"{account_id} ({server})", "inline": True},
                {"name": "Equity / Balance", "value": f"${equity:,.2f} / ${balance:,.2f}", "inline": True},
                {"name": "Risk / Trade", "value": f"{risk_pct:.2f}%", "inline": True},
                {"name": "Daily DD (Limit / CB)", "value": f"-{daily_dd:.1f}%" + (f" / -{daily_cb:.1f}%" if daily_cb else ""), "inline": True},
                {"name": "Max DD (Limit / CB)", "value": f"-{max_dd:.1f}%" + (f" / -{max_cb:.1f}%" if max_cb else ""), "inline": True},
                {"name": "Active Assets", "value": ", ".join(symbols), "inline": False}
            ],
            "footer": {"text": f"Started at {now_str}"}
        }
        if strategy_desc:
            discord_embed["fields"].append({"name": "Strategy Setup", "value": strategy_desc, "inline": False})

        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_shutdown(self, reason: str = "User Stopped"):
        """Notifies when the bot cleanly stops."""
        now_str = format_dual_time(include_date=True)
        tg_text = f"🛑 <b>MASTER UNIFIED BOT SHUTDOWN</b>\n\n• <b>Reason:</b> {reason}\n• <b>Time:</b> {now_str}"
        discord_embed = {
            "title": "🛑 MASTER UNIFIED BOT SHUTDOWN",
            "description": f"**Reason:** {reason}",
            "color": 0xFF5555,
            "footer": {"text": f"Stopped at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_session(self, session_name: str, status: str):
        """Notifies session open / transition updates (London Open, NY Open)."""
        now_str = format_dual_time(include_date=False)
        tg_text = f"⏰ <b>SESSION UPDATE: {session_name} {status}</b>\nTime: {now_str}"
        discord_embed = {
            "title": f"⏰ SESSION UPDATE: {session_name} {status}",
            "color": 0x3399FF,
            "footer": {"text": f"Time: {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_trading_paused(self, zone_title: str, reason: str, resume_time_str: str, is_startup: bool = False):
        """Notifies when surveillance and execution are paused (Asian session, Trap hours, Weekend)."""
        now_str = format_dual_time(include_date=False)
        res_dual = format_dual_hhmm(resume_time_str)
        prefix = "CURRENT STATUS: " if is_startup else ""

        tg_text = (
            f"⏸️ <b>{prefix}TRADING PAUSED: {zone_title}</b>\n\n"
            f"• <b>Reason:</b> {reason}\n"
            f"• <b>Status:</b> Scanning & trade execution suspended to protect capital\n"
            f"• <b>Resumes:</b> {res_dual}\n"
            f"• <b>Time:</b> {now_str}"
        )

        discord_embed = {
            "title": f"⏸️ {prefix}TRADING PAUSED: {zone_title}",
            "description": f"**Status:** Scanning & trade execution suspended to protect capital.\n**Reason:** {reason}",
            "color": 0xFFAA00,
            "fields": [
                {"name": "Status", "value": "PAUSED", "inline": True},
                {"name": "Resumes At", "value": res_dual, "inline": True}
            ],
            "footer": {"text": f"Paused at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_trading_resumed(
        self,
        zone_title: str,
        session_name: str,
        details: str = "Active market surveillance and trade execution restored."
    ):
        """Notifies when surveillance and execution resume after a trap or session transition."""
        now_str = format_dual_time(include_date=False)

        tg_text = (
            f"▶️ <b>TRADING RESUMED: {zone_title}</b>\n\n"
            f"• <b>Active Window:</b> {session_name}\n"
            f"• <b>Status:</b> {details}\n"
            f"• <b>Time:</b> {now_str}"
        )

        discord_embed = {
            "title": f"▶️ TRADING RESUMED: {zone_title}",
            "description": f"**Status:** {details}",
            "color": 0x00FF88,
            "fields": [
                {"name": "Active Window", "value": session_name, "inline": True},
                {"name": "Status", "value": "ACTIVE", "inline": True}
            ],
            "footer": {"text": f"Resumed at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_trade_opened(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        spread: float,
        total_lots: float,
        partial_lots: Optional[float] = None,
        runner_lots: Optional[float] = None,
        sl: float = 0.0,
        tp1: float = 0.0,
        tp2: float = 0.0,
        ticket_a: Optional[int] = None,
        ticket_b: Optional[int] = None,
        is_dry_run: bool = False,
        splits: Optional[List[Dict[str, Any]]] = None,
        tickets: Optional[List[int]] = None,
        strategy: str = "ApexHunter"
    ):
        """
        Notifies when a new trade is executed.
        Supports both 2-way twin splits (ApexHunter) and 3-way splits (Lewis Kelly SMC).
        """
        icon = "🟢" if direction == "BUY" else "🔴"
        mode_lbl = " [SIMULATED]" if is_dry_run else ""
        now_str = format_dual_time(include_date=True)

        # Multi-split format (e.g. 3-way SMC or provided splits list)
        if splits and len(splits) > 0:
            split_lines_tg = []
            split_fields_dc = []
            for i, sp in enumerate(splits):
                t_lbl = f"#{tickets[i]}" if (tickets and i < len(tickets)) else f"Split {i+1}"
                vol = sp.get('volume', 0.0)
                tp_val = sp.get('tp', 0.0)
                lbl = sp.get('label', f'Tier {i+1}')
                split_lines_tg.append(f"• <b>{lbl} ({t_lbl}):</b> {vol:.2f}L -> TP @ {tp_val:,.5f}")
                split_fields_dc.append(f"`{t_lbl}` ({lbl}): **{vol:.2f}L** -> TP `{tp_val:,.5f}`")

            splits_str_tg = "\n".join(split_lines_tg)
            splits_str_dc = "\n".join(split_fields_dc)

            tg_text = (
                f"{icon} <b>NEW TRADE ENTERED: {symbol} {direction}{mode_lbl}</b>\n\n"
                f"• <b>Strategy:</b> {strategy}\n"
                f"• <b>Entry Price:</b> {entry_price:,.5f} (Spread: {spread:.2f})\n"
                f"• <b>Total Volume:</b> {total_lots:.2f} lots\n"
                f"• <b>Stop Loss (SL):</b> {sl:,.5f}\n"
                f"<b>Server-Side Split Targets:</b>\n{splits_str_tg}\n"
                f"• <b>Time:</b> {now_str}"
            )

            discord_embed = {
                "title": f"{icon} NEW TRADE ENTERED: {symbol} {direction}{mode_lbl}",
                "description": f"**Strategy:** {strategy}\n**Entry:** `{entry_price:,.5f}` (Spread: `{spread:.2f}`)",
                "color": 0x00FF88 if direction == "BUY" else 0xFF4444,
                "fields": [
                    {"name": "Total Volume", "value": f"**{total_lots:.2f} lots**", "inline": True},
                    {"name": "Stop Loss", "value": f"`{sl:,.5f}`", "inline": True},
                    {"name": "Split Targets", "value": splits_str_dc, "inline": False}
                ],
                "footer": {"text": f"Filled at {now_str}"}
            }
        else:
            # Twin-ticket format (ApexHunter: Ticket A 50% TP1 + Ticket B 50% TP2)
            p_lots = partial_lots if partial_lots is not None else (total_lots * 0.5)
            r_lots = runner_lots if runner_lots is not None else (total_lots - p_lots)
            t_a = f"#{ticket_a}" if ticket_a else "Sim_A"
            t_b = f"#{ticket_b}" if ticket_b else "Sim_B"

            tg_text = (
                f"{icon} <b>NEW TRADE ENTERED: {symbol} {direction}{mode_lbl}</b>\n\n"
                f"• <b>Strategy:</b> {strategy}\n"
                f"• <b>Entry Price:</b> {entry_price:,.2f} (Spread: {spread:.2f})\n"
                f"• <b>Total Volume:</b> {total_lots:.2f} lots (A: {p_lots:.2f} | B: {r_lots:.2f})\n"
                f"• <b>Stop Loss (SL):</b> {sl:,.2f}\n"
                f"• <b>Target 1 (TP1 50%):</b> {tp1:,.2f}\n"
                f"• <b>Target 2 (Runner TP2):</b> {tp2:,.2f}\n"
                f"• <b>Tickets:</b> {t_a} (Part) / {t_b} (Runner)\n"
                f"• <b>Time:</b> {now_str}"
            )

            discord_embed = {
                "title": f"{icon} NEW TRADE ENTERED: {symbol} {direction}{mode_lbl}",
                "color": 0x00FF88 if direction == "BUY" else 0xFF4444,
                "fields": [
                    {"name": "Entry", "value": f"{entry_price:,.2f} (Spr: {spread:.2f})", "inline": True},
                    {"name": "Lots", "value": f"Total: {total_lots:.2f} (A: {p_lots:.2f} / B: {r_lots:.2f})", "inline": True},
                    {"name": "Stop Loss", "value": f"{sl:,.2f}", "inline": True},
                    {"name": "Target 1 (TP1 50%)", "value": f"{tp1:,.2f}", "inline": True},
                    {"name": "Target 2 (TP2 Runner)", "value": f"{tp2:,.2f}", "inline": True},
                    {"name": "Order Tickets", "value": f"A: {t_a} | B: {t_b}", "inline": True}
                ],
                "footer": {"text": f"Filled at {now_str}"}
            }

        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_tp1_breakeven(
        self,
        symbol: str,
        ticket_a: Any,
        ticket_b: Any,
        be_sl: float,
        profit_a: float = 0.0,
        strategy: str = "ApexHunter"
    ):
        """Notifies when Ticket A hits TP1 and Runner SL is moved to Breakeven (+0.2R / Entry + Spread)."""
        now_str = format_dual_time(include_date=False)
        pnl_str = f"• 💰 <b>Profit Banked (Order 1):</b> <b>{format_dollar(profit_a)}</b>\n" if profit_a != 0.0 else ""

        if isinstance(ticket_b, list):
            b_desc = ", ".join([f"#{t}" for t in ticket_b])
            b_field = ", ".join([f"`#{t}`" for t in ticket_b])
        else:
            b_desc = f"#{ticket_b}"
            b_field = f"`#{ticket_b}`"

        tg_text = (
            f"🎯 <b>TARGET 1 HIT: {symbol} (+PROFIT LOCKED)</b>\n\n"
            f"• Strategy: <b>{strategy}</b>\n"
            f"• Order #{ticket_a} closed at TP1!\n"
            f"{pnl_str}"
            f"• 🛡️ <b>BREAKEVEN AUTOMATOR:</b> Sibling order(s) {b_desc} Stop Loss shifted to <b>{be_sl:,.5f}</b> (Entry + Buffer)!\n"
            f"• <b>Status: Trade is now 100% RISK-FREE!</b>\n"
            f"• <b>Time:</b> {now_str}"
        )

        discord_fields = [
            {"name": "Order 1 Closed", "value": f"#{ticket_a} (TP1 Hit)", "inline": True},
            {"name": "Sibling Orders", "value": b_field, "inline": True},
            {"name": "Runner SL (BE)", "value": f"`{be_sl:,.5f}`", "inline": True}
        ]
        if profit_a != 0.0:
            discord_fields.append({"name": "Profit Locked", "value": f"**{format_dollar(profit_a)}**", "inline": True})
        else:
            discord_fields.append({"name": "Status", "value": "100% Risk-Free", "inline": True})

        pnl_desc = f"**Profit Banked: {format_dollar(profit_a)}**\n" if profit_a != 0.0 else ""
        discord_embed = {
            "title": f"🎯 TARGET 1 HIT: {symbol} (+PROFIT LOCKED)",
            "description": f"Order #{ticket_a} hit TP1!\n{pnl_desc}🛡️ Sibling orders ({b_desc}) SL shifted to **{be_sl:,.5f}**.\n**Trade is now 100% Risk-Free!**",
            "color": 0x00FFAA,
            "fields": discord_fields,
            "footer": {"text": f"Locked at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_trade_closed(
        self,
        symbol: str,
        ticket: Any,
        exit_reason: str,
        pnl: float,
        ticket_a: Optional[int] = None,
        pnl_a: Optional[float] = None,
        pnl_b: Optional[float] = None,
        day_pnl: Optional[float] = None,
        day_pnl_pct: Optional[float] = None,
        daily_dd_pct: Optional[float] = None,
        remaining_cushion: Optional[float] = None,
        daily_cb_pct: Optional[float] = None,
        order_breakdown: Optional[List[Dict[str, Any]]] = None,
        strategy: Optional[str] = None
    ):
        """Notifies when a position or group is closed with full breakdown & daily cushion metrics."""
        now_str = format_dual_time(include_date=False)

        clean_reason = exit_reason.upper()
        if "TP" in clean_reason or "TAKE PROFIT" in clean_reason or "TARGET" in clean_reason:
            icon = "🎯"
            color = 0x00FF88
            header_status = "TARGET HIT (WINNER)"
        elif "BREAKEVEN" in clean_reason:
            icon = "🛡️"
            color = 0x00D4FF
            header_status = "EXIT AT BREAKEVEN"
        elif pnl < 0 or "STOP LOSS" in clean_reason or "SL" in clean_reason:
            icon = "🛑"
            color = 0xFF4444
            header_status = "STOP LOSS HIT"
        else:
            icon = "🚀" if pnl > 0 else ("🛡️" if abs(pnl) < 1.0 else "🛑")
            color = 0x00FF88 if pnl > 0 else (0x888888 if abs(pnl) < 1.0 else 0xFF4444)
            header_status = "TRADE CLOSED"

        breakdown_lines_tg = []
        breakdown_lines_dc = []
        if order_breakdown:
            for item in order_breakdown:
                lbl = item.get("label", "Order")
                t_no = item.get("ticket", "")
                p_val = item.get("pnl", 0.0)
                breakdown_lines_tg.append(f"• <b>{lbl} (#{t_no}):</b> {format_dollar(p_val)}")
                breakdown_lines_dc.append(f"`#{t_no}` ({lbl}): **{format_dollar(p_val)}**")
        elif pnl_a is not None and pnl_b is not None and ticket_a is not None:
            breakdown_lines_tg.append(f"• <b>Order A (#{ticket_a}):</b> {format_dollar(pnl_a)}")
            breakdown_lines_tg.append(f"• <b>Order B (#{ticket}):</b> {format_dollar(pnl_b)}")
            breakdown_lines_dc.append(f"Order A (`#{ticket_a}`): `{format_dollar(pnl_a)}`")
            breakdown_lines_dc.append(f"Order B (`#{ticket}`): `{format_dollar(pnl_b)}`")

        breakdown_str_tg = ("\n" + "\n".join(breakdown_lines_tg) + "\n") if breakdown_lines_tg else ""

        daily_lines_tg = []
        if day_pnl is not None:
            pct_str = f" ({day_pnl_pct:+.2f}%)" if day_pnl_pct is not None else ""
            daily_lines_tg.append(f"• <b>Today's Net PnL:</b> <b>{format_dollar(day_pnl)}</b>{pct_str}")
        if daily_dd_pct is not None:
            daily_lines_tg.append(f"• <b>Daily Drawdown:</b> -{daily_dd_pct:.2f}%")
        if remaining_cushion is not None:
            cb_str = f"{daily_cb_pct:.1f}% CB" if daily_cb_pct else "Circuit Breaker"
            daily_lines_tg.append(f"• <b>Daily CB Cushion:</b> ${remaining_cushion:,.2f} remaining before {cb_str}")
        daily_str_tg = ("\n📊 <b>Daily Performance Summary:</b>\n" + "\n".join(daily_lines_tg) + "\n") if daily_lines_tg else ""

        strat_str = f" ({strategy})" if strategy else ""
        tg_text = (
            f"{icon} <b>TRADE CLOSED: {symbol}{strat_str} — {header_status}</b>\n\n"
            f"• <b>Outcome:</b> {exit_reason}\n"
            f"{breakdown_str_tg}"
            f"• <b>Total Trade PnL:</b> <b>{format_dollar(pnl)}</b>\n"
            f"{daily_str_tg}"
            f"• <b>Closed at:</b> {now_str}"
        )

        discord_fields = [
            {"name": "Outcome", "value": exit_reason, "inline": True},
            {"name": "Total Trade PnL", "value": f"**{format_dollar(pnl)}**", "inline": True}
        ]

        if breakdown_lines_dc:
            discord_fields.append({
                "name": "Order Breakdown",
                "value": "\n".join(breakdown_lines_dc),
                "inline": False
            })

        if day_pnl is not None:
            pct_str = f" ({day_pnl_pct:+.2f}%)" if day_pnl_pct is not None else ""
            discord_fields.append({
                "name": "Today's Net PnL",
                "value": f"**{format_dollar(day_pnl)}**{pct_str}",
                "inline": True
            })

        if daily_dd_pct is not None:
            discord_fields.append({
                "name": "Daily Drawdown",
                "value": f"`-{daily_dd_pct:.2f}%`",
                "inline": True
            })

        if remaining_cushion is not None:
            cb_str = f"{daily_cb_pct:.1f}% CB" if daily_cb_pct else "Circuit Breaker"
            discord_fields.append({
                "name": "Remaining CB Cushion",
                "value": f"`${remaining_cushion:,.2f}` (to {cb_str})",
                "inline": True
            })

        discord_embed = {
            "title": f"{icon} TRADE CLOSED: {symbol}{strat_str} — {header_status}",
            "color": color,
            "fields": discord_fields,
            "footer": {"text": f"Closed at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_daily_summary(
        self,
        date_str: str,
        starting_equity: float,
        closing_equity: float,
        balance: float,
        trades_count: int,
        winning_trades: int,
        losing_trades: int,
        daily_pnl: float,
        daily_pnl_pct: float,
        daily_dd_pct: float,
        cushion_remaining: float,
        closed_trades_details: Optional[List[Dict[str, Any]]] = None
    ):
        """Broadcasts end-of-day scorecard at market close (21:05 UTC) with PnL & cushions."""
        pnl_str = format_dollar(daily_pnl, force_sign=True)
        sign_char = "+" if daily_pnl >= 0 else "-"
        pnl_pct_str = f"{sign_char}{abs(daily_pnl_pct):.2f}%"
        now_str = format_dual_time(include_date=True)

        if daily_pnl > 0:
            status_icon = "🟢"
            status_text = "PROFITABLE SESSION"
            discord_color = 0x00FF88
        elif daily_pnl < 0:
            status_icon = "🔴"
            status_text = "DEFENSIVE DRAWDOWN (PROTECTED)"
            discord_color = 0xFF4444
        else:
            status_icon = "⚪"
            status_text = "CAPITAL PRESERVED (FLAT)"
            discord_color = 0x3399FF

        tg_lines = [
            f"{status_icon} <b>MASTER UNIFIED DAILY PERFORMANCE SCORECARD</b>",
            f"<b>Date:</b> <code>{date_str}</code> | <b>Status:</b> <code>{status_text}</code>\n",
            f"💰 <b>Net Realized PnL:</b> <code>{pnl_str} ({pnl_pct_str})</code>",
            f"📊 <b>Total Trades Today:</b> <code>{trades_count}</code> (✅ {winning_trades} W | ❌ {losing_trades} L)",
            f"📉 <b>Daily Peak DD:</b> <code>-{daily_dd_pct:.2f}%</code>",
            f"🛡️ <b>Remaining CB Cushion:</b> <code>${cushion_remaining:,.2f}</code>\n",
            f"🏦 <b>Starting Balance Anchor:</b> <code>${starting_equity:,.2f}</code>",
            f"💼 <b>Closing Equity:</b> <code>${closing_equity:,.2f}</code>",
            f"💳 <b>Account Balance:</b> <code>${balance:,.2f}</code>\n",
        ]

        if closed_trades_details and len(closed_trades_details) > 0:
            tg_lines.append("<b>Closed Trades Breakdown:</b>")
            for tr in closed_trades_details:
                tr_pnl = format_dollar(tr.get("profit", 0.0))
                tg_lines.append(
                    f"• #{tr.get('ticket', 'N/A')} {tr.get('symbol', '')} {tr.get('type', '')} "
                    f"{tr.get('volume', '')}L: <b>{tr_pnl}</b> ({tr.get('comment', '')})"
                )
        else:
            tg_lines.append("<i>Zero setups triggered today. Capital was 100% protected.</i>")

        tg_lines.append(f"\n🕒 <i>Session closed at {now_str}</i>")
        tg_text = "\n".join(tg_lines)

        discord_fields = [
            {"name": "Net Realized PnL", "value": f"**{pnl_str}** ({pnl_pct_str})", "inline": True},
            {"name": "Trades Count", "value": f"**{trades_count}** ({winning_trades}W / {losing_trades}L)", "inline": True},
            {"name": "Daily DD", "value": f"**-{daily_dd_pct:.2f}%**", "inline": True},
            {"name": "Starting Anchor", "value": f"${starting_equity:,.2f}", "inline": True},
            {"name": "Closing Equity", "value": f"${closing_equity:,.2f}", "inline": True},
            {"name": "CB Cushion Remaining", "value": f"${cushion_remaining:,.2f}", "inline": True},
        ]

        if closed_trades_details and len(closed_trades_details) > 0:
            trade_summary_lines = []
            for tr in closed_trades_details[:8]:
                tr_pnl = format_dollar(tr.get("profit", 0.0))
                trade_summary_lines.append(
                    f"`#{tr.get('ticket', 'N/A')}` **{tr.get('symbol', '')}** {tr.get('type', '')} "
                    f"({tr.get('volume', '')}L) -> **{tr_pnl}**"
                )
            discord_fields.append({
                "name": "Closed Trades Detail",
                "value": "\n".join(trade_summary_lines),
                "inline": False
            })
        else:
            discord_fields.append({
                "name": "Trade Activity",
                "value": "Zero strategy setups met strict institutional criteria today. Capital 100% preserved.",
                "inline": False
            })

        discord_embed = {
            "title": f"🌙 Daily Performance & PnL Summary ({date_str})",
            "description": f"**Session Status:** `{status_text}`\nInstitutional session close reached. All risk cushions verified.",
            "color": discord_color,
            "fields": discord_fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": f"Master Unified Trading System • {now_str}"}
        }

        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_nightly_audit(
        self,
        title: str,
        summary_text: str,
        discord_fields: Optional[List[Dict[str, Any]]] = None,
        markdown_report: Optional[str] = None
    ):
        """Broadcasts nightly reconciliation scorecard comparing backtest setups vs live broker execution."""
        now_str = format_dual_time(include_date=True)
        clean_desc = (
            summary_text
            .replace("<b>", "**").replace("</b>", "**")
            .replace("<code>", "`").replace("</code>", "`")
            .replace("<i>", "*").replace("</i>", "*")
        )

        discord_embed = {
            "title": title,
            "description": clean_desc[:2048],
            "color": 0x2ECC71,
            "fields": discord_fields or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": f"Master Unified Nightly Reconciler • {now_str}"}
        }

        self._enqueue({"text": summary_text, "content": "", "embed": discord_embed})

    def notify_circuit_breaker(
        self,
        cb_type: str,
        current_equity: float,
        limit_pct: float,
        loss_amount: float,
        hard_limit_pct: Optional[float] = None
    ):
        """Emergency alert when a circuit breaker triggers."""
        now_str = format_dual_time(include_date=True)
        hard_note = f" (Hard Limit: -{hard_limit_pct:.1f}%)" if hard_limit_pct is not None else ""

        if cb_type == "daily":
            tg_text = (
                f"🚨 <b>DAILY CIRCUIT BREAKER TRIGGERED (-{limit_pct:.1f}%){hard_note}</b> 🚨\n\n"
                f"• <b>Current Equity:</b> ${current_equity:,.2f}\n"
                f"• <b>Today's Loss:</b> -${loss_amount:,.2f} (-{limit_pct:.1f}%)\n"
                f"• <b>ACTION: Trading HALTED for remainder of day.</b> Account protected before hitting hard DD limit.\n"
                f"• <b>Time:</b> {now_str}"
            )
            discord_embed = {
                "title": f"🚨 DAILY CIRCUIT BREAKER TRIGGERED (-{limit_pct:.1f}%){hard_note} 🚨",
                "description": f"**Trading HALTED for remainder of day.** Protected before hitting hard DD limit.\nCurrent Equity: ${current_equity:,.2f} | Loss: -${loss_amount:,.2f}",
                "color": 0xFF9900,
                "footer": {"text": f"Triggered at {now_str}"}
            }
        else:
            tg_text = (
                f"🚨🚨 <b>EMERGENCY: MAX ACCOUNT FLOOR CIRCUIT BREAKER (-{limit_pct:.1f}%){hard_note} REACHED</b> 🚨🚨\n\n"
                f"• <b>Current Equity:</b> ${current_equity:,.2f}\n"
                f"• <b>ACTION: All positions liquidated. Bot permanently halted to protect against hard limit breach.</b>\n"
                f"• <b>Time:</b> {now_str}"
            )
            discord_embed = {
                "title": f"🚨🚨 EMERGENCY: MAX ACCOUNT FLOOR CIRCUIT BREAKER (-{limit_pct:.1f}%){hard_note} REACHED 🚨🚨",
                "description": f"**All positions liquidated. Bot permanently halted before hitting hard DD limit.**\nCurrent Equity: ${current_equity:,.2f}",
                "color": 0xFF0000,
                "footer": {"text": f"Triggered at {now_str}"}
            }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_news_shield_activated(self, title: str, country: str, release_time_str: str, resume_time_str: str):
        """Notifies when the High-Impact News Shield is activated."""
        now_str = format_dual_time(include_date=False)
        rel_dual = format_dual_hhmm(release_time_str)
        res_dual = format_dual_hhmm(resume_time_str)

        tg_text = (
            f"⚠️ <b>HIGH-IMPACT NEWS SHIELD ACTIVATED</b>\n\n"
            f"• <b>Event:</b> {country} - {title}\n"
            f"• <b>Release Time:</b> {rel_dual}\n"
            f"• <b>Trading Suspended:</b> Embargo active before & after news\n"
            f"• <b>Resumes At:</b> {res_dual}\n"
            f"• <b>Status:</b> Trade arming & entries paused to protect against spread spikes.\n"
            f"• <b>Time:</b> {now_str}"
        )
        discord_embed = {
            "title": f"⚠️ HIGH-IMPACT NEWS SHIELD ACTIVATED ({country})",
            "description": f"**Event:** {country} - {title}\n**Release Time:** {rel_dual}\n**Trading Suspended:** Until {res_dual}",
            "color": 0xFFAA00,
            "fields": [
                {"name": "Impact", "value": "HIGH (Red Folder)", "inline": True},
                {"name": "Status", "value": "Trade Arming Paused", "inline": True},
                {"name": "Resumes", "value": res_dual, "inline": True}
            ],
            "footer": {"text": f"Activated at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_news_shield_lifted(self, title: str, resume_time_str: str):
        """Notifies when the High-Impact News Shield is lifted."""
        now_str = format_dual_time(include_date=False)
        res_dual = format_dual_hhmm(resume_time_str)

        tg_text = (
            f"✅ <b>HIGH-IMPACT NEWS SHIELD LIFTED</b>\n\n"
            f"• <b>Event Passed:</b> {title}\n"
            f"• <b>Resumed At:</b> {res_dual}\n"
            f"• <b>Status:</b> Normal market surveillance and trade execution restored.\n"
            f"• <b>Time:</b> {now_str}"
        )
        discord_embed = {
            "title": "✅ HIGH-IMPACT NEWS SHIELD LIFTED",
            "description": f"**Event Passed:** {title}\n**Resumed At:** {res_dual}\n**Status:** Trading resumed. Normal market surveillance restored.",
            "color": 0x00FF88,
            "footer": {"text": f"Lifted at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_weekend_close(self, closed_trades: List[Dict[str, Any]], total_pnl: float, resume_time_str: str):
        """Notifies when Friday weekend square-off flattens all positions."""
        now_str = format_dual_time(include_date=False)
        pnl_str = f"• 💰 <b>Total Realized PnL:</b> <b>{format_dollar(total_pnl)}</b>\n" if total_pnl != 0.0 else ""

        trade_bullets = []
        discord_fields = []
        for tr in closed_trades:
            sym = tr.get("symbol", "")
            t_id = tr.get("ticket", "")
            vol = tr.get("volume", "")
            pnl = tr.get("pnl", 0.0)
            trade_bullets.append(f"  - {sym} #{t_id} ({vol} lots) | PnL: {format_dollar(pnl)}")
            discord_fields.append({
                "name": f"{sym} #{t_id}",
                "value": f"Volume: `{vol}` | PnL: `{format_dollar(pnl)}`",
                "inline": True
            })

        trades_text = "\n".join(trade_bullets) if trade_bullets else "• <i>No open positions were active (Account was already 100% flat).</i>"

        tg_text = (
            f"🛡️ <b>WEEKEND CAPITAL SHIELD: POSITIONS SQUARED OFF</b>\n\n"
            f"• <b>Reason:</b> Friday pre-close (16:45 NY / Dynamic DST sync)\n"
            f"• <b>Positions Squared Off:</b>\n{trades_text}\n"
            f"{pnl_str}"
            f"• <b>Account Status:</b> 100% Flat (Zero Monday Gap Risk)\n"
            f"• <b>Resumes:</b> {resume_time_str}\n"
            f"• <b>Time:</b> {now_str}"
        )

        if not discord_fields:
            discord_fields.append({
                "name": "Positions Squared Off",
                "value": "Account was already 100% flat (Zero open risk).",
                "inline": False
            })

        discord_fields.append({
            "name": "Safety Status",
            "value": "🔒 **100% Capital Preserved** (Zero Weekend Gap Risk)",
            "inline": False
        })
        discord_fields.append({
            "name": "Resumes",
            "value": resume_time_str,
            "inline": False
        })

        discord_embed = {
            "title": "🛡️ WEEKEND CAPITAL SHIELD: POSITIONS SQUARED OFF",
            "description": "All open positions cleanly closed 15 minutes before Friday market close (**Dynamic NY DST Sync**).\nAccount capital is **100% flat** with zero Monday gap exposure.",
            "color": 0x33CCFF,
            "fields": discord_fields,
            "footer": {"text": f"Executed at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_challenge_passed(
        self,
        account_id: str,
        phase: Any,
        target_pct: float,
        current_balance: float,
        profit_dollar: float,
        profit_pct: float,
        is_final: bool = False,
        server: str = "MT5"
    ):
        """Notifies when a prop firm challenge target is reached."""
        now_str = format_dual_time(include_date=True)
        is_final_eval = is_final or str(phase).lower() in ["2", "funded"]

        if is_final_eval:
            tg_text = (
                f"👑🏆 <b>PROP FIRM CHALLENGE FULLY PASSED!</b> 🏆👑\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎉 <b>CONGRATULATIONS! ALL CHALLENGE PHASES COMPLETED!</b> 🎉\n\n"
                f"• <b>Account:</b> <code>{account_id}</code> ({server})\n"
                f"• <b>Final Milestone:</b> Phase {phase} Target (+{target_pct:.1f}%) REACHED on Closed Balance!\n"
                f"• <b>Closed Balance:</b> <b>${current_balance:,.2f}</b>\n"
                f"• <b>Challenge Profit:</b> +${profit_dollar:,.2f} (+{profit_pct:.2f}%)\n"
                f"• <b>Protection Status:</b> 🔒 <b>TRADING PERMANENTLY HALTED (Pass Locked)</b>\n\n"
                f"<b>NEXT STEPS TO GET FUNDED:</b>\n"
                f"1. Submit credentials to prop firm to claim your <b>FUNDED ACCOUNT</b>.\n"
                f"2. Switch bot lifecycle to <b>Funded Mode</b> in menu: <code>[3] Manage Lifecycle & Risk</code>.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ <i>Evaluation passed at {now_str}</i>"
            )
            discord_embed = {
                "title": "👑🏆 PROP FIRM CHALLENGE FULLY PASSED! (FUNDED READY) 🏆👑",
                "description": "🎉 **CONGRATULATIONS! EVALUATION 100% COMPLETED!** 🎉\n\nAll challenge milestones have been reached on **Realized Closed Balance**.",
                "color": 0xFFD700,
                "fields": [
                    {"name": "Account", "value": f"`{account_id}` ({server})", "inline": True},
                    {"name": "Closed Balance", "value": f"`${current_balance:,.2f}`", "inline": True},
                    {"name": "Total Profit", "value": f"`+${profit_dollar:,.2f} (+{profit_pct:.2f}%)`", "inline": True},
                    {"name": "Protection Status", "value": "🔒 **Trading Permanently Halted** (Zero Risk of Drawdown)", "inline": False},
                    {"name": "Next Steps to Get Funded", "value": "1. Submit account credentials to claim your Funded Account.\n2. In bot menu: `[3] Manage Lifecycle & Risk` -> switch to **Funded Mode** (1.00% Risk).", "inline": False}
                ],
                "footer": {"text": f"Evaluation passed at {now_str}"}
            }
        else:
            tg_text = (
                f"🏆 <b>PHASE 1 EVALUATION TARGET PASSED!</b> 🏆\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Account:</b> <code>{account_id}</code> ({server})\n"
                f"• <b>Phase 1 Milestone:</b> +{target_pct:.1f}% REACHED on Closed Balance!\n"
                f"• <b>Realized Balance:</b> <b>${current_balance:,.2f}</b>\n"
                f"• <b>Phase 1 Profit:</b> +${profit_dollar:,.2f} (+{profit_pct:.2f}%)\n"
                f"• <b>Protection Status:</b> 🛡️ <b>TRADING HALTED FOR TODAY</b> (Daily Profit Secured)\n\n"
                f"<b>NEXT STEPS:</b>\n"
                f"• Advance to Phase 2 in bot menu: <code>[3] Manage Lifecycle & Risk</code> -> <code>[1] Advance Active Phase</code>.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ <i>Milestone reached at {now_str}</i>"
            )
            discord_embed = {
                "title": f"🏆 PHASE 1 EVALUATION TARGET PASSED! (+{target_pct:.1f}%) 🏆",
                "description": "**Phase 1 milestone reached on Realized Closed Balance!**\n\nTrading is **halted for the remainder of today** to secure profits and prevent intraday overtrading.",
                "color": 0x00D084,
                "fields": [
                    {"name": "Account", "value": f"`{account_id}` ({server})", "inline": True},
                    {"name": "Realized Balance", "value": f"`${current_balance:,.2f}`", "inline": True},
                    {"name": "Phase 1 Profit", "value": f"`+${profit_dollar:,.2f} (+{profit_pct:.2f}%)`", "inline": True},
                    {"name": "Protection Status", "value": "🛡️ **Trading Halted for Remainder of Today**", "inline": False},
                    {"name": "Next Steps", "value": "Advance to Phase 2 in bot menu: `[3] Manage Lifecycle & Risk` -> `[1] Advance Active Phase`.", "inline": False}
                ],
                "footer": {"text": f"Milestone reached at {now_str}"}
            }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_phase_transition(
        self,
        account_id: str,
        new_phase_name: str,
        start_balance: float,
        target_pct: float,
        active_risk_pct: float,
        is_custom_risk_on: bool = False,
        server: str = "MT5"
    ):
        """Notifies when an account is transitioned to Phase 2 or Funded mode."""
        now_str = format_dual_time(include_date=True)
        scaling_desc = f"Phase Scaling ON ({active_risk_pct:.2f}%)" if is_custom_risk_on else f"Fixed Risk ({active_risk_pct:.2f}%)"
        target_str = f"+{target_pct:.1f}% Milestone Target" if target_pct > 0 else "Bi-Weekly Payout Target (Capital Preservation)"

        tg_text = (
            f"🚀 <b>ACCOUNT LIFECYCLE PHASE UPDATED!</b> 🚀\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Account:</b> <code>{account_id}</code> ({server})\n"
            f"• <b>New Active Phase:</b> <b>{new_phase_name.upper()}</b>\n"
            f"• <b>Phase Start Balance:</b> ${start_balance:,.2f}\n"
            f"• <b>Target Milestone:</b> {target_str}\n"
            f"• <b>Active Sizing Risk:</b> {active_risk_pct:.2f}% [{scaling_desc}]\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ <i>Updated at {now_str}</i>"
        )
        discord_embed = {
            "title": f"🚀 ACCOUNT LIFECYCLE UPDATED: {new_phase_name.upper()} 🚀",
            "description": f"Account `{account_id}` has been advanced to **{new_phase_name.upper()}**.",
            "color": 0x3498DB,
            "fields": [
                {"name": "Account", "value": f"`{account_id}` ({server})", "inline": True},
                {"name": "New Phase", "value": f"`{new_phase_name.upper()}`", "inline": True},
                {"name": "Start Balance", "value": f"`${start_balance:,.2f}`", "inline": True},
                {"name": "Milestone Target", "value": f"`{target_str}`", "inline": False},
                {"name": "Active Sizing Risk", "value": f"`{active_risk_pct:.2f}%` ({scaling_desc})", "inline": False}
            ],
            "footer": {"text": f"Phase updated at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})


# Compatibility alias
NotificationManager = TradeNotifier
