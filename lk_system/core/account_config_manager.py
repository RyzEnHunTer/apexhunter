"""
Account Config & Lifecycle Persistence Manager
==============================================
Location: core/account_config_manager.py

Provides persistent storage for MT5 account profiles:
1. Lifecycle tracking: Challenge Mode (Phase 1, Phase 2) vs Funded Account (Live Profit Share)
2. Phase target tracking (e.g. +8.0% Phase 1, +5.0% Phase 2) with automated target locking
3. Starting balance anchors per phase
4. Phase-specific risk per trade (Challenge Risk % vs Funded Risk %)
5. High-Water Mark (peak equity) tracking
6. Drawdown & Circuit Breaker cushions per account

All settings are permanently remembered across restarts in `bot_accounts_config.json`.
"""

import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "live", "bot_accounts_config.json")


def load_base_notification_defaults() -> Dict[str, str]:
    """Loads default notification credentials from live/config.json if available."""
    config_path = os.path.join(PROJECT_ROOT, "live", "config.json")
    defaults = {
        "active_platform": "all",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "discord_webhook_url": ""
    }
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                n = d.get("notifications", {})
                if n.get("discord_webhook_url"):
                    defaults["discord_webhook_url"] = n.get("discord_webhook_url").strip()
                if n.get("telegram_bot_token"):
                    defaults["telegram_bot_token"] = n.get("telegram_bot_token").strip()
                if n.get("telegram_chat_id"):
                    defaults["telegram_chat_id"] = n.get("telegram_chat_id").strip()
                if defaults["discord_webhook_url"] and defaults["telegram_bot_token"]:
                    defaults["active_platform"] = "all"
                elif defaults["discord_webhook_url"]:
                    defaults["active_platform"] = "discord"
                elif defaults["telegram_bot_token"]:
                    defaults["active_platform"] = "telegram"
        except Exception:
            pass
    return defaults


class AccountConfigManager:
    """Manages persistent per-account prop firm lifecycle, targets, and risk configuration."""

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            self.config_path = DEFAULT_CONFIG_PATH
        else:
            self.config_path = config_path

        os.makedirs(os.path.dirname(os.path.abspath(self.config_path)), exist_ok=True)
        self.accounts: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        """Loads account configuration from JSON file."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[AccountConfigManager WARN] Failed to load config from {self.config_path}: {e}. Initializing fresh.")
        return {}

    def save(self):
        """Atomically saves account configuration to JSON file."""
        try:
            tmp_path = f"{self.config_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.accounts, f, indent=2)
            os.replace(tmp_path, self.config_path)
        except Exception as e:
            print(f"[AccountConfigManager ERROR] Could not save config to {self.config_path}: {e}")

    def get_or_setup_account(self, account_info: Any, auto_defaults: bool = False) -> Dict[str, Any]:
        """
        Retrieves existing account config or initializes a new profile with intelligent defaults.
        """
        acc_id = str(account_info.login)
        balance = float(account_info.balance)
        equity = float(account_info.equity)
        server = str(getattr(account_info, "server", "MT5"))
        company = str(getattr(account_info, "company", "Broker"))

        if acc_id in self.accounts:
            cfg = self.accounts[acc_id]
            needs_save = False

            # Ensure all canonical lifecycle fields exist
            defaults = {
                "server": server,
                "broker_company": company,
                "account_lifecycle": "challenge",
                "challenge_steps": 2,
                "current_phase": 1,
                "phase_start_balance": round(balance, 2),
                "phase_1_target_pct": 8.0,
                "phase_2_target_pct": 5.0,
                "challenge_risk_pct": 1.25,
                "funded_risk_pct": 1.00,
                "use_custom_phase_risk": False,
                "risk_per_trade": 0.010,
                "daily_dd_limit_pct": 4.0,
                "daily_cb_pct": 3.0,
                "max_total_dd_pct": 8.5,
                "max_cb_pct": 8.5,
                "max_dd_type": "static",
                "high_water_mark": round(equity, 2),
                "target_locked": False,
                "notifications": load_base_notification_defaults(),
            }

            for key, val in defaults.items():
                if key not in cfg:
                    cfg[key] = val
                    needs_save = True

            # Ensure notifications has all keys
            if "notifications" not in cfg or not isinstance(cfg["notifications"], dict):
                cfg["notifications"] = load_base_notification_defaults()
                needs_save = True

            # Sync active risk_per_trade based on scaling toggle
            use_custom = cfg.get("use_custom_phase_risk", False)
            if use_custom:
                active_r = cfg.get("challenge_risk_pct", 1.25) if cfg.get("account_lifecycle") == "challenge" else cfg.get("funded_risk_pct", 1.00)
            else:
                active_r = 1.00

            expected_r_dec = round(active_r / 100.0, 4)
            if cfg.get("risk_per_trade") != expected_r_dec:
                cfg["risk_per_trade"] = expected_r_dec
                needs_save = True

            if equity > cfg.get("high_water_mark", 0.0):
                cfg["high_water_mark"] = round(equity, 2)
                needs_save = True

            if needs_save:
                cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()

            return cfg

        # Brand New Account Setup
        cfg = {
            "account_id": acc_id,
            "server": server,
            "broker_company": company,
            "account_lifecycle": "challenge",
            "challenge_steps": 2,
            "current_phase": 1,
            "phase_start_balance": round(balance, 2),
            "phase_1_target_pct": 8.0,
            "phase_2_target_pct": 5.0,
            "challenge_risk_pct": 1.25,
            "funded_risk_pct": 1.00,
            "use_custom_phase_risk": False,
            "risk_per_trade": 0.010,
            "daily_dd_limit_pct": 4.0,
            "daily_cb_pct": 3.0,
            "max_total_dd_pct": 8.5,
            "max_cb_pct": 8.5,
            "max_dd_type": "static",
            "high_water_mark": round(equity, 2),
            "target_locked": False,
            "notifications": load_base_notification_defaults(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        self.accounts[acc_id] = cfg
        self.save()
        print(f"[AccountConfigManager] Initialized persistent settings for Account {acc_id} in bot_accounts_config.json")
        return cfg

    def advance_account_phase(
        self,
        acc_id: str,
        new_phase: Any,
        new_start_bal: Optional[float] = None,
        notifier: Optional[Any] = None
    ) -> bool:
        """
        Advances account from Phase 1 -> Phase 2 or to Funded Mode, automatically updating anchors and risk.
        """
        if acc_id not in self.accounts:
            return False

        cfg = self.accounts[acc_id]
        cfg["target_locked"] = False
        use_custom = cfg.get("use_custom_phase_risk", False)
        phase_name = ""
        target_pct = 0.0

        if str(new_phase).lower() in ["funded", "3"]:
            cfg["account_lifecycle"] = "funded"
            cfg["current_phase"] = "funded"
            active_r = cfg.get("funded_risk_pct", 1.00) if use_custom else 1.00
            cfg["risk_per_trade"] = round(active_r / 100.0, 4)
            phase_name = "Funded Account (Profit Share)"
            target_pct = 0.0
            if new_start_bal is not None:
                cfg["phase_start_balance"] = round(float(new_start_bal), 2)
            print(f"\n[PHASE ADVANCE] Account {acc_id} transitioned to FUNDED MODE!")
            print(f"  * Active Risk: {active_r:.2f}% (Custom Scaling: {'ON' if use_custom else 'OFF (1.00%)'})")

        elif str(new_phase) == "2":
            cfg["account_lifecycle"] = "challenge"
            cfg["current_phase"] = 2
            if new_start_bal is not None:
                cfg["phase_start_balance"] = round(float(new_start_bal), 2)
            active_r = cfg.get("challenge_risk_pct", 1.25) if use_custom else 1.00
            cfg["risk_per_trade"] = round(active_r / 100.0, 4)
            phase_name = "Challenge Phase 2"
            target_pct = cfg.get("phase_2_target_pct", 5.0)
            print(f"\n[PHASE ADVANCE] Account {acc_id} advanced to CHALLENGE PHASE 2!")
            print(f"  * Phase 2 Starting Balance Anchor: ${cfg['phase_start_balance']:,.2f}")
            print(f"  * Phase 2 Target:                 +{target_pct:.1f}%")
            print(f"  * Active Risk:                    {active_r:.2f}% (Custom Scaling: {'ON' if use_custom else 'OFF (1.00%)'})")

        elif str(new_phase) == "1":
            cfg["account_lifecycle"] = "challenge"
            cfg["current_phase"] = 1
            if new_start_bal is not None:
                cfg["phase_start_balance"] = round(float(new_start_bal), 2)
            active_r = cfg.get("challenge_risk_pct", 1.25) if use_custom else 1.00
            cfg["risk_per_trade"] = round(active_r / 100.0, 4)
            phase_name = "Challenge Phase 1"
            target_pct = cfg.get("phase_1_target_pct", 8.0)
            print(f"\n[PHASE ADVANCE] Account {acc_id} set to CHALLENGE PHASE 1!")
            print(f"  * Phase 1 Starting Balance Anchor: ${cfg['phase_start_balance']:,.2f}")
            print(f"  * Phase 1 Target:                 +{target_pct:.1f}%")
            print(f"  * Active Risk:                    {active_r:.2f}% (Custom Scaling: {'ON' if use_custom else 'OFF (1.00%)'})")

        cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save()

        if notifier and hasattr(notifier, "send_message"):
            start_b = cfg.get("phase_start_balance", 0.0)
            notifier.send_message(
                f"🏆 <b>[PROP FIRM LIFECYCLE UPDATE]</b>\n"
                f"Account: <code>{acc_id}</code> ({cfg.get('server', 'MT5')})\n"
                f"New Active Phase: <b>{phase_name}</b>\n"
                f"Starting Balance Anchor: <b>${start_b:,.2f}</b>\n"
                f"Milestone Target: <b>+{target_pct:.1f}%</b>\n"
                f"Active Risk Per Trade: <b>{cfg.get('risk_per_trade', 0.01)*100.0:.2f}%</b>"
            )

        return True

    def update_lifecycle_settings(
        self,
        acc_id: str,
        lifecycle: str,
        current_phase: Any,
        p1_target: float,
        p2_target: float,
        ch_risk: float,
        funded_risk: float,
        start_bal: Optional[float] = None
    ) -> bool:
        """Updates phase evaluation targets, risk scaling parameters, and starting balance."""
        if acc_id not in self.accounts:
            return False

        cfg = self.accounts[acc_id]
        cfg["account_lifecycle"] = lifecycle.lower()
        cfg["current_phase"] = current_phase
        cfg["phase_1_target_pct"] = round(float(p1_target), 2)
        cfg["phase_2_target_pct"] = round(float(p2_target), 2)
        cfg["challenge_risk_pct"] = round(float(ch_risk), 2)
        cfg["funded_risk_pct"] = round(float(funded_risk), 2)

        if start_bal is not None:
            cfg["phase_start_balance"] = round(float(start_bal), 2)

        use_custom = cfg.get("use_custom_phase_risk", False)
        active_r = (ch_risk if cfg["account_lifecycle"] == "challenge" else funded_risk) if use_custom else 1.00
        cfg["risk_per_trade"] = round(active_r / 100.0, 4)
        cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save()
        return True

    def toggle_custom_phase_risk(self, acc_id: str) -> bool:
        """Toggles between custom phase risk scaling and default fixed 1.00% risk."""
        if acc_id not in self.accounts:
            return False

        cfg = self.accounts[acc_id]
        curr = cfg.get("use_custom_phase_risk", False)
        cfg["use_custom_phase_risk"] = not curr

        if cfg["use_custom_phase_risk"]:
            active_r = cfg.get("challenge_risk_pct", 1.25) if cfg.get("account_lifecycle") == "challenge" else cfg.get("funded_risk_pct", 1.00)
        else:
            active_r = 1.00

        cfg["risk_per_trade"] = round(active_r / 100.0, 4)
        cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save()
        return cfg["use_custom_phase_risk"]

    def update_high_water_mark(self, acc_id: str, equity: float) -> float:
        """Updates and persists the peak equity high-water mark."""
        if acc_id not in self.accounts:
            return equity

        cfg = self.accounts[acc_id]
        if equity > cfg.get("high_water_mark", 0.0):
            cfg["high_water_mark"] = round(float(equity), 2)
            cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
        return cfg.get("high_water_mark", equity)

    def set_target_locked(self, acc_id: str, locked: bool = True):
        """Locks the account when target is reached to prevent overtrading."""
        if acc_id in self.accounts:
            self.accounts[acc_id]["target_locked"] = locked
            self.accounts[acc_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()

    def check_target_progress(self, acc_id: str, current_balance: float) -> Tuple[bool, Dict[str, Any]]:
        """
        Calculates live challenge target progress, remaining dollars to goal,
        and returns whether the phase milestone target has been successfully achieved.
        """
        if acc_id not in self.accounts:
            return False, {}

        cfg = self.accounts[acc_id]
        lifecycle = cfg.get("account_lifecycle", "challenge").lower()
        curr_phase = str(cfg.get("current_phase", 1))
        ch_steps = cfg.get("challenge_steps", 2)
        start_bal = cfg.get("phase_start_balance", current_balance)
        p1_tgt = cfg.get("phase_1_target_pct", 8.0)
        p2_tgt = cfg.get("phase_2_target_pct", 5.0)

        profit_dollar = current_balance - start_bal
        profit_pct = (profit_dollar / start_bal * 100.0) if start_bal > 0 else 0.0

        if lifecycle == "challenge":
            active_target_pct = p1_tgt if curr_phase == "1" else p2_tgt
            target_dollar = start_bal * (1.0 + active_target_pct / 100.0)
            remaining_dollar = max(0.0, target_dollar - current_balance)
            rem_pct = max(0.0, active_target_pct - profit_pct)
            is_reached = current_balance >= target_dollar

            return is_reached, {
                "lifecycle": "CHALLENGE",
                "phase": curr_phase,
                "challenge_steps": ch_steps,
                "start_balance": start_bal,
                "current_balance": current_balance,
                "profit_dollar": profit_dollar,
                "profit_pct": profit_pct,
                "active_target_pct": active_target_pct,
                "target_dollar": target_dollar,
                "remaining_dollar": remaining_dollar,
                "remaining_pct": rem_pct,
                "is_reached": is_reached,
                "target_locked": cfg.get("target_locked", False)
            }
        else:
            return False, {
                "lifecycle": "FUNDED",
                "phase": "funded",
                "challenge_steps": ch_steps,
                "start_balance": start_bal,
                "current_balance": current_balance,
                "profit_dollar": profit_dollar,
                "profit_pct": profit_pct,
                "active_target_pct": 0.0,
                "target_dollar": 0.0,
                "remaining_dollar": 0.0,
                "remaining_pct": 0.0,
                "is_reached": False,
                "target_locked": False
            }

    def get_notification_config(self, acc_id: str) -> Dict[str, Any]:
        """Returns notification settings for specified account."""
        if acc_id in self.accounts and "notifications" in self.accounts[acc_id]:
            return self.accounts[acc_id]["notifications"]
        return load_base_notification_defaults()

    def update_notification_settings(
        self,
        acc_id: str,
        platform: str,
        tg_token: Optional[str] = None,
        tg_chat_id: Optional[str] = None,
        discord_url: Optional[str] = None
    ) -> bool:
        """Updates and persists notification credentials for specified account."""
        if acc_id not in self.accounts:
            return False

        if "notifications" not in self.accounts[acc_id]:
            self.accounts[acc_id]["notifications"] = load_base_notification_defaults()

        n_cfg = self.accounts[acc_id]["notifications"]
        n_cfg["active_platform"] = platform.lower()
        if tg_token is not None:
            n_cfg["telegram_bot_token"] = tg_token.strip()
        if tg_chat_id is not None:
            n_cfg["telegram_chat_id"] = tg_chat_id.strip()
        if discord_url is not None:
            n_cfg["discord_webhook_url"] = discord_url.strip()

        self.accounts[acc_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save()

        # Also sync back to live/config.json so centralized config stays in sync
        try:
            live_cfg_path = os.path.join(PROJECT_ROOT, "live", "config.json")
            if os.path.exists(live_cfg_path):
                with open(live_cfg_path, "r", encoding="utf-8") as f:
                    master_cfg = json.load(f)
                master_cfg["notifications"] = {
                    "active_platform": n_cfg["active_platform"],
                    "discord_webhook_url": n_cfg.get("discord_webhook_url", ""),
                    "telegram_bot_token": n_cfg.get("telegram_bot_token", ""),
                    "telegram_chat_id": n_cfg.get("telegram_chat_id", "")
                }
                with open(live_cfg_path, "w", encoding="utf-8") as f:
                    json.dump(master_cfg, f, indent=2)
        except Exception as e:
            print(f"[AccountConfigManager WARN] Failed to sync config.json notifications: {e}")

        return True
