"""
Institutional Remote Notification Engine
========================================
Location: live/notifier.py

Supports dual-engine remote notifications:
1. Telegram Bot (Bot Token & Chat ID)
2. Discord Webhook (Webhook URL)

Features:
- Configurable Platform: "all" (both), "telegram", "discord", or "none" (muted)
- Non-blocking: Dispatches all HTTP requests asynchronously in worker threads
- Live Connection Testing: Synchronous verification method for interactive menus
- SSL Resilience: Automatic fallback to unverified SSL context if OS cert store is missing
- Zero external dependencies beyond standard library urllib
"""

import os
import json
import ssl
import urllib.request
import urllib.error
import urllib.parse
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple

TZ_IST = timezone(timedelta(hours=5, minutes=30))


def _get_ssl_context() -> ssl.SSLContext:
    """Returns an SSL context that prioritizes certifi CA bundle with unverified fallback."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def format_dual_time(dt: Optional[datetime] = None) -> str:
    """Formats datetime into dual UTC + IST string for clear local time visibility."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    dt_ist = dt.astimezone(TZ_IST)
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} UTC ({dt_ist.strftime('%Y-%m-%d %H:%M:%S')} IST)"


class TradeNotifier:
    """Manages remote alerts for Telegram and Discord asynchronously."""

    def __init__(self, config_or_path: Optional[Any] = None, config_path: Optional[Any] = None):
        self.active_platform = "all"
        self.discord_url = ""
        self.tg_token = ""
        self.tg_chat_id = ""

        cfg_target = config_or_path if config_or_path is not None else config_path
        if isinstance(cfg_target, dict):
            self.update_config(cfg_target)
        else:
            self._load_from_path(cfg_target)

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
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    notif = data.get("notifications", {})
                    self.update_config(notif)
            except Exception as e:
                print(f"[TradeNotifier WARN] Failed to load config from {config_path}: {e}")

    def update_config(self, notif_cfg: Dict[str, Any]):
        """Dynamically updates active credentials and platform filter."""
        # Check if notifications is nested
        if "notifications" in notif_cfg and isinstance(notif_cfg["notifications"], dict):
            notif_cfg = notif_cfg["notifications"]

        self.active_platform = str(notif_cfg.get("active_platform", self.active_platform)).lower()
        self.discord_url = str(notif_cfg.get("discord_webhook_url", self.discord_url)).strip()
        self.tg_token = str(notif_cfg.get("telegram_bot_token", self.tg_token)).strip()
        self.tg_chat_id = str(notif_cfg.get("telegram_chat_id", self.tg_chat_id)).strip()

    @classmethod
    def _send_discord_http(cls, webhook_url: str, message: str, timeout: int = 5) -> Tuple[bool, str]:
        if not webhook_url:
            return False, "Discord Webhook URL is empty."

        # Convert HTML tags to Discord markdown
        discord_msg = message.replace('<b>', '**').replace('</b>', '**')
        discord_msg = discord_msg.replace('<i>', '*').replace('</i>', '*')
        discord_msg = discord_msg.replace('<code>', '`').replace('</code>', '`')

        data = {"content": discord_msg}
        body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers={'Content-Type': 'application/json', 'User-Agent': 'MasterUnifiedBot/2.0'},
            method='POST'
        )

        ctx = _get_ssl_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                if response.status in (200, 204):
                    return True, "Discord notification sent successfully."
                return False, f"HTTP Status {response.status}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode('utf-8', errors='ignore')
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

    @classmethod
    def _send_telegram_http(cls, token: str, chat_id: str, message: str, timeout: int = 5) -> Tuple[bool, str]:
        if not token or not chat_id:
            return False, "Telegram Bot Token or Chat ID is empty."

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=body,
            headers={'Content-Type': 'application/json', 'User-Agent': 'MasterUnifiedBot/2.0'},
            method='POST'
        )

        ctx = _get_ssl_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                if response.status == 200:
                    return True, "Telegram notification sent successfully."
                return False, f"HTTP Status {response.status}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode('utf-8', errors='ignore')
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
    def test_connection(cls, platform: str, token_or_url: str, chat_id: Optional[str] = None) -> Tuple[bool, str]:
        """Synchronous connection test triggered from interactive menu."""
        test_msg = (
            f"🔔 <b>[REMOTE NOTIFICATION VERIFICATION]</b>\n"
            f"Status: 🟢 <b>Connection Operational!</b>\n"
            f"Dual Time: {format_dual_time()}\n"
            f"Master Unified Institutional Trading Station"
        )
        p = platform.lower()
        if p == "telegram":
            return cls._send_telegram_http(token_or_url.strip(), (chat_id or "").strip(), test_msg, timeout=8)
        elif p == "discord":
            return cls._send_discord_http(token_or_url.strip(), test_msg, timeout=8)
        return False, f"Unknown platform: {platform}"

    def send_message(self, message: str):
        """Sends a message asynchronously to active platform(s) without blocking execution."""
        plat = self.active_platform
        if plat == "none":
            return

        threads = []
        if plat in ("all", "both", "discord") and self.discord_url:
            t_disc = threading.Thread(target=self._send_discord_http, args=(self.discord_url, message), daemon=True)
            threads.append(t_disc)

        if plat in ("all", "both", "telegram") and self.tg_token and self.tg_chat_id:
            t_tg = threading.Thread(target=self._send_telegram_http, args=(self.tg_token, self.tg_chat_id, message), daemon=True)
            threads.append(t_tg)

        for t in threads:
            t.start()

    def send_test(self) -> bool:
        """Sends a startup test notification."""
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


# Compatibility alias
NotificationManager = TradeNotifier
