"""
Bot Persistence Manager & Crash Recovery Engine
===============================================
Location: core/persistence_manager.py

Provides zero-loss state persistence and auto-recovery for MetaTrader 5 bot instances:
1. Atomic JSON State Persistence (anti-corruption via temp-file swap).
2. Live State Lifecycle: Tracks multi-split position groups (APE_T1/APE_T2, SMC_TP1/2/3).
3. Post-Restart Reconciliation with MT5 positions_get():
   - Resolves true trade direction ('BUY' for type 0, 'SELL' for type 1, strictly never 'ACTIVE').
   - Detects if Order 1 already closed at TP1 while offline, ensuring runner SL remains at Breakeven.
   - Adopts orphan bot positions from MT5 if state file is unavailable.
4. Intraday Risk Anchor Restoration:
   - Restores 00:00 UTC day_start_balance and consecutive loss counters on same-day restart.
   - Prevents accidental reset of the 4.0% daily circuit breaker.
"""

import os
import json
import logging
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any


class BotPersistenceManager:
    """
    Manages persistent state across bot restarts, crashes, or terminal disconnects.
    """

    def __init__(self, state_file_path: Optional[str] = None, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("BotPersistenceManager")
        if state_file_path:
            self.state_file_path = os.path.abspath(state_file_path)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.state_file_path = os.path.abspath(os.path.join(base_dir, "logs", "bot_runtime_state.json"))

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.state_file_path), exist_ok=True)

    def load_state(self) -> Dict[str, Any]:
        """Loads bot runtime state from disk safely. Returns empty state structure on failure."""
        if not os.path.exists(self.state_file_path):
            return {"open_positions": {}, "daily_risk_state": None, "last_updated": None}

        try:
            with open(self.state_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return {"open_positions": {}, "daily_risk_state": None, "last_updated": None}
                data.setdefault("open_positions", {})
                data.setdefault("daily_risk_state", None)
                return data
        except Exception as e:
            self.logger.warning(f"[PersistenceManager] Failed to read state file ({e}). Returning empty state.")
            return {"open_positions": {}, "daily_risk_state": None, "last_updated": None}

    def save_state(self, state: Dict[str, Any]) -> bool:
        """
        Atomically saves state using a temporary file and rename to prevent corrupted writes.
        """
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        target_dir = os.path.dirname(self.state_file_path)
        os.makedirs(target_dir, exist_ok=True)

        try:
            # Create a temp file in the same directory to ensure atomic replace on all OS filesystems
            with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8") as tf:
                json.dump(state, tf, indent=2, default=str)
                temp_name = tf.name

            # Atomically replace destination file
            os.replace(temp_name, self.state_file_path)
            return True
        except Exception as e:
            self.logger.error(f"[PersistenceManager] Atomic state write failed: {e}")
            if 'temp_name' in locals() and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except Exception:
                    pass
            return False

    def save_position(self, group_id: str, pos_data: Dict[str, Any]):
        """Persists or updates an active position group."""
        state = self.load_state()
        state["open_positions"][group_id] = pos_data
        self.save_state(state)

    def get_position(self, group_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a persisted position group by group_id."""
        state = self.load_state()
        return state["open_positions"].get(group_id)

    def update_position_tp1(self, group_id: str, new_sl: float):
        """Marks a position's TP1 as hit and updates SL to Breakeven."""
        state = self.load_state()
        if group_id in state["open_positions"]:
            pos = state["open_positions"][group_id]
            pos["tp1_hit"] = True
            pos["sl"] = new_sl
            pos["be_sl"] = new_sl
            self.save_state(state)

    def remove_position(self, group_id: str):
        """Removes a position group once closed."""
        state = self.load_state()
        if group_id in state["open_positions"]:
            del state["open_positions"][group_id]
            self.save_state(state)

    def save_daily_risk(
        self,
        date_str: str,
        day_start_balance: float,
        daily_halt_active: bool,
        symbol_consecutive_losses: Dict[str, int],
        symbol_daily_halted: Dict[str, bool],
        day_start_equity: Optional[float] = None,
        day_start_anchor: Optional[float] = None,
        daily_drawdown_floor: Optional[float] = None
    ):
        """Persists daily risk tracking anchors and counters."""
        state = self.load_state()
        eq = day_start_equity if day_start_equity is not None else day_start_balance
        anchor = day_start_anchor if day_start_anchor is not None else max(day_start_balance, eq)
        floor = daily_drawdown_floor if daily_drawdown_floor is not None else (anchor - (5000.0 * 0.04))

        state["daily_risk_state"] = {
            "date_str": date_str,
            "day_start_balance": float(day_start_balance),
            "day_start_equity": float(eq),
            "day_start_anchor": float(anchor),
            "daily_drawdown_floor": float(floor),
            "daily_halt_active": bool(daily_halt_active),
            "symbol_consecutive_losses": dict(symbol_consecutive_losses),
            "symbol_daily_halted": dict(symbol_daily_halted),
            "saved_at": datetime.now(timezone.utc).isoformat()
        }
        self.save_state(state)

    def recover_state(
        self,
        live_positions: List[Any],
        order_manager: Any,
        risk_guardian: Any,
        asset_handler: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Reconciles persisted state against live MT5 positions and restores intraday risk state.
        
        Returns:
            Dict containing:
                "recovered_count": int,
                "open_positions": Dict[str, Dict[str, Any]],
                "risk_restored": bool
        """
        state = self.load_state()
        saved_positions = state.get("open_positions", {})
        saved_risk = state.get("daily_risk_state")

        # 1. Restore Daily Risk State if restarting on the same UTC day
        risk_restored = False
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if saved_risk and saved_risk.get("date_str") == today_str:
            try:
                risk_guardian.current_utc_date = datetime.strptime(today_str, "%Y-%m-%d").date()
                bal = float(saved_risk["day_start_balance"])
                eq = float(saved_risk.get("day_start_equity", bal))
                anchor = float(saved_risk.get("day_start_anchor", max(bal, eq)))
                floor = float(saved_risk.get("daily_drawdown_floor", anchor - (risk_guardian.baseline_capital * risk_guardian.daily_cb_pct)))

                risk_guardian.day_start_balance = bal
                risk_guardian.day_start_equity = eq
                risk_guardian.day_start_anchor = anchor
                risk_guardian.daily_drawdown_floor = floor
                risk_guardian.daily_halt_active = bool(saved_risk.get("daily_halt_active", False))
                risk_guardian.symbol_consecutive_losses = dict(saved_risk.get("symbol_consecutive_losses", {}))
                risk_guardian.symbol_daily_halted = dict(saved_risk.get("symbol_daily_halted", {}))
                risk_restored = True
                self.logger.info(
                    f"[PersistenceManager] Restored same-day risk anchor: Start Bal=${bal:,.2f}, Equity=${eq:,.2f}, "
                    f"Max Anchor=${anchor:,.2f}, Floor=${floor:,.2f}, Halt={risk_guardian.daily_halt_active}"
                )
            except Exception as e:
                self.logger.warning(f"[PersistenceManager] Failed to restore risk state: {e}")

        # 2. Reconcile Positions
        live_tickets_map = {p.ticket: p for p in (live_positions or [])}
        recovered_open_positions: Dict[str, Dict[str, Any]] = {}
        matched_live_tickets: set = set()

        # Reconcile saved position groups
        remaining_saved_positions: Dict[str, Any] = {}

        for group_id, pos in list(saved_positions.items()):
            saved_tickets = pos.get("tickets", [])
            live_sibling_tickets = [t for t in saved_tickets if t in live_tickets_map]

            if live_sibling_tickets:
                # Group has active tickets in MT5
                matched_live_tickets.update(live_sibling_tickets)

                # Check if Order 1 (TP1) already closed while offline
                t0 = saved_tickets[0] if saved_tickets else None
                tp1_hit = pos.get("tp1_hit", False)
                if t0 and t0 not in live_tickets_map and len(saved_tickets) > 1:
                    # Order 1 closed!
                    tp1_hit = True
                    pos["tp1_hit"] = True
                    pos["sl"] = pos.get("be_sl", pos.get("sl"))

                # Register into order_manager
                symbol = pos["symbol"]
                order_manager.register_position_group(
                    group_id=group_id,
                    strategy=pos.get("strategy", "UNKNOWN"),
                    symbol=symbol,
                    entry_price=pos["entry_price"],
                    initial_sl=pos.get("initial_sl", pos["sl"]),
                    be_sl=pos["be_sl"],
                    tickets=saved_tickets
                )

                if tp1_hit and group_id in order_manager.active_groups:
                    grp = order_manager.active_groups[group_id]
                    be_target = pos.get("be_sl", pos.get("sl"))
                    dir_check = pos.get("direction", "BUY").upper()

                    all_already_be = True
                    for t in live_sibling_tickets:
                        p_live = live_tickets_map.get(t)
                        if p_live:
                            # Check if MT5 position already has Breakeven SL applied
                            is_at_be = (
                                (p_live.sl >= be_target - 1e-4)
                                if dir_check == "BUY"
                                else (p_live.sl <= be_target + 1e-4 and p_live.sl > 0)
                            )
                            if is_at_be:
                                grp["modified_tickets"].add(t)
                            else:
                                all_already_be = False

                    grp["be_updated"] = all_already_be

                # Normalize direction (ensure BUY or SELL, never ACTIVE)
                direction = pos.get("direction", "BUY").upper()
                if direction not in ("BUY", "SELL"):
                    # Deduce from live position
                    first_live_p = live_tickets_map[live_sibling_tickets[0]]
                    direction = "BUY" if first_live_p.type == 0 else "SELL"
                pos["direction"] = direction

                recovered_open_positions[symbol] = pos
                remaining_saved_positions[group_id] = pos
            else:
                # All tickets closed while offline. Do not keep in active positions.
                self.logger.info(f"[PersistenceManager] Position group '{group_id}' closed offline. Pruning.")

        # 3. Check for orphan MT5 bot positions (e.g. state file was lost or manual MT5 restart)
        valid_magics = set(range(order_manager.magic_base + 1, order_manager.magic_base + 10))
        orphan_positions: List[Any] = []

        for p in (live_positions or []):
            if p.ticket not in matched_live_tickets:
                is_bot_magic = getattr(p, "magic", 0) in valid_magics
                comment = str(getattr(p, "comment", "")).upper()
                is_bot_comment = any(k in comment for k in ["DCC", "LK", "APE", "SMC", "LEW"])

                if is_bot_magic or is_bot_comment:
                    orphan_positions.append(p)

        if orphan_positions:
            # Group by symbol
            orphan_groups: Dict[str, List[Any]] = {}
            for p in orphan_positions:
                orphan_groups.setdefault(p.symbol, []).append(p)

            for broker_sym, pos_list in orphan_groups.items():
                pos_list.sort(key=lambda x: getattr(x, "magic", 0))
                p0 = pos_list[0]
                tickets = [p.ticket for p in pos_list]

                # Resolve canonical symbol
                if asset_handler:
                    canon = asset_handler.get_canonical_symbol(broker_sym) or broker_sym
                else:
                    canon = broker_sym

                group_id = f"RECOVERED_{canon}_{p0.ticket}"
                strategy = "APEXHUNTER_1_2_1" if any(s in canon.upper() for s in ["XAU", "GOLD", "NAS", "USTEC"]) else "LEWIS_KELLY_SMC"
                entry_p = float(p0.price_open)
                sl_p = float(p0.sl)
                direction = "BUY" if p0.type == 0 else "SELL"
                sl_dist = abs(entry_p - sl_p) if sl_p > 0 else 0.0
                be_sl = entry_p + (0.2 * sl_dist) if direction == "BUY" else entry_p - (0.2 * sl_dist)

                # Check if comments or magic suggest runner only (TP1 closed)
                comment0 = str(getattr(p0, "comment", "")).upper()
                tp1_hit = (
                    any(k in comment0 for k in ["RUNNER", "TP2", "TP3", "_T2", "_T3"])
                    or getattr(p0, "magic", 0) > (order_manager.magic_base + 1)
                )

                pos_data = {
                    "group_id": group_id,
                    "strategy": strategy,
                    "symbol": canon,
                    "broker_sym": broker_sym,
                    "direction": direction,
                    "entry_price": entry_p,
                    "initial_sl": sl_p,
                    "sl": sl_p,
                    "be_sl": be_sl,
                    "tp1_hit": tp1_hit,
                    "tickets": tickets,
                    "splits": [],
                    "entry_time": datetime.now(timezone.utc).isoformat()
                }

                order_manager.register_position_group(
                    group_id=group_id,
                    strategy=strategy,
                    symbol=canon,
                    entry_price=entry_p,
                    initial_sl=sl_p,
                    be_sl=be_sl,
                    tickets=tickets
                )

                if tp1_hit and group_id in order_manager.active_groups:
                    order_manager.active_groups[group_id]["be_updated"] = True

                recovered_open_positions[canon] = pos_data
                remaining_saved_positions[group_id] = pos_data
                self.logger.info(f"[PersistenceManager] Adopted orphan MT5 position group '{group_id}' ({direction} on {canon}).")

        # Update disk state to pruned/reconciled positions
        state["open_positions"] = remaining_saved_positions
        self.save_state(state)

        return {
            "recovered_count": len(recovered_open_positions),
            "open_positions": recovered_open_positions,
            "risk_restored": risk_restored
        }
