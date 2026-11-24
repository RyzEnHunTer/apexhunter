"""
Unit Tests for BotPersistenceManager (Atomic State Persistence & Auto-Recovery)
Verifies:
1. Atomic JSON save and load of bot runtime state (anti-corruption).
2. Position lifecycle: open, update TP1 / BE shift, position removal on close.
3. Crash recovery of multi-split positions (Gold/Nasdaq 2 splits, Forex 3 splits).
4. Accurate trade direction extraction ('BUY' / 'SELL', strictly never 'ACTIVE').
5. Detection of pre-closed Order 1 (TP1 already hit before restart).
6. Same-day risk anchor and loss counter restoration (preventing 4.0% CB bypass).
7. Rollover behavior when restarting on a new calendar day.
8. Handling of corrupted or missing state files gracefully.
"""

import unittest
import os
import sys
import json
import shutil
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.persistence_manager import BotPersistenceManager
from core.order_manager import OrderManager
from core.risk_guardian import RiskGuardian


class MockMT5Position:
    def __init__(self, ticket, symbol, type_code, magic, price_open, sl, tp, volume, comment=""):
        self.ticket = ticket
        self.symbol = symbol
        self.type = type_code  # 0 = BUY, 1 = SELL
        self.magic = magic
        self.price_open = price_open
        self.sl = sl
        self.tp = tp
        self.volume = volume
        self.comment = comment


class TestPersistenceManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.test_dir, "test_bot_runtime_state.json")
        self.manager = BotPersistenceManager(state_file_path=self.state_file)
        self.order_mgr = OrderManager(magic_base=20260900)
        self.risk_guardian = RiskGuardian(baseline_capital=5000.0)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_atomic_save_and_load(self):
        pos_data = {
            "group_id": "GRP_XAU_1",
            "strategy": "APEXHUNTER_1_2_1",
            "symbol": "XAUUSD",
            "broker_sym": "GOLD",
            "direction": "BUY",
            "entry_price": 2650.0,
            "sl": 2640.0,
            "be_sl": 2652.0,
            "tp1_hit": False,
            "tickets": [101, 102],
            "entry_time": datetime.now(timezone.utc).isoformat()
        }
        self.manager.save_position("GRP_XAU_1", pos_data)
        
        # Verify file exists on disk
        self.assertTrue(os.path.exists(self.state_file))
        loaded = self.manager.load_state()
        self.assertIn("GRP_XAU_1", loaded["open_positions"])
        self.assertEqual(loaded["open_positions"]["GRP_XAU_1"]["direction"], "BUY")

    def test_position_lifecycle(self):
        pos_data = {
            "group_id": "GRP_EUR_1",
            "strategy": "LEWIS_KELLY_SMC",
            "symbol": "EURUSD",
            "broker_sym": "EURUSD",
            "direction": "SELL",
            "entry_price": 1.0500,
            "sl": 1.0530,
            "be_sl": 1.0494,
            "tp1_hit": False,
            "tickets": [201, 202, 203]
        }
        self.manager.save_position("GRP_EUR_1", pos_data)
        self.assertFalse(self.manager.get_position("GRP_EUR_1")["tp1_hit"])

        # Update TP1
        self.manager.update_position_tp1("GRP_EUR_1", new_sl=1.0494)
        updated = self.manager.get_position("GRP_EUR_1")
        self.assertTrue(updated["tp1_hit"])
        self.assertEqual(updated["sl"], 1.0494)

        # Remove on close
        self.manager.remove_position("GRP_EUR_1")
        self.assertIsNone(self.manager.get_position("GRP_EUR_1"))

    def test_startup_recovery_full_groups(self):
        # Saved state has 1 active Gold trade with 2 tickets
        pos_data = {
            "group_id": "GRP_XAU_1",
            "strategy": "APEXHUNTER_1_2_1",
            "symbol": "XAUUSD",
            "broker_sym": "GOLD",
            "direction": "BUY",
            "entry_price": 2650.0,
            "sl": 2640.0,
            "be_sl": 2652.0,
            "tp1_hit": False,
            "tickets": [101, 102]
        }
        self.manager.save_position("GRP_XAU_1", pos_data)

        # Mock MT5 positions: both tickets 101 and 102 are alive
        live_mt5_positions = [
            MockMT5Position(ticket=101, symbol="GOLD", type_code=0, magic=20260901, price_open=2650.0, sl=2640.0, tp=2665.0, volume=0.1, comment="APE_T1"),
            MockMT5Position(ticket=102, symbol="GOLD", type_code=0, magic=20260902, price_open=2650.0, sl=2640.0, tp=2680.0, volume=0.1, comment="APE_T2")
        ]

        result = self.manager.recover_state(
            live_positions=live_mt5_positions,
            order_manager=self.order_mgr,
            risk_guardian=self.risk_guardian
        )

        self.assertEqual(result["recovered_count"], 1)
        rec_pos = result["open_positions"]["XAUUSD"]
        self.assertEqual(rec_pos["direction"], "BUY")
        self.assertNotEqual(rec_pos["direction"], "ACTIVE")
        self.assertFalse(rec_pos["tp1_hit"])
        self.assertEqual(rec_pos["tickets"], [101, 102])
        self.assertIn("GRP_XAU_1", self.order_mgr.active_groups)

    def test_startup_recovery_tp1_already_hit(self):
        # State had tickets [101, 102]. While offline, Ticket 101 closed at TP1!
        pos_data = {
            "group_id": "GRP_XAU_1",
            "strategy": "APEXHUNTER_1_2_1",
            "symbol": "XAUUSD",
            "broker_sym": "GOLD",
            "direction": "BUY",
            "entry_price": 2650.0,
            "sl": 2652.0,
            "be_sl": 2652.0,
            "tp1_hit": False,
            "tickets": [101, 102]
        }
        self.manager.save_position("GRP_XAU_1", pos_data)

        # MT5 only returns ticket 102 (runner)
        live_mt5_positions = [
            MockMT5Position(ticket=102, symbol="GOLD", type_code=0, magic=20260902, price_open=2650.0, sl=2652.0, tp=2680.0, volume=0.1, comment="APE_T2")
        ]

        result = self.manager.recover_state(
            live_positions=live_mt5_positions,
            order_manager=self.order_mgr,
            risk_guardian=self.risk_guardian
        )

        self.assertEqual(result["recovered_count"], 1)
        rec_pos = result["open_positions"]["XAUUSD"]
        self.assertTrue(rec_pos["tp1_hit"])  # Must detect TP1 was hit!
        self.assertEqual(rec_pos["sl"], 2652.0)
        self.assertEqual(rec_pos["direction"], "BUY")
        # Ensure order manager knows Order 1 closed
        self.assertTrue(self.order_mgr.active_groups["GRP_XAU_1"]["be_updated"])

    def test_orphan_mt5_position_recovery(self):
        # No saved state on disk, but MT5 has open trades from bot
        live_mt5_positions = [
            MockMT5Position(ticket=301, symbol="EURUSD", type_code=1, magic=20260901, price_open=1.0500, sl=1.0530, tp=1.0450, volume=0.2, comment="SMC_TP1"),
            MockMT5Position(ticket=302, symbol="EURUSD", type_code=1, magic=20260902, price_open=1.0500, sl=1.0530, tp=1.0400, volume=0.2, comment="SMC_TP2"),
        ]

        result = self.manager.recover_state(
            live_positions=live_mt5_positions,
            order_manager=self.order_mgr,
            risk_guardian=self.risk_guardian
        )

        self.assertEqual(result["recovered_count"], 1)
        rec_pos = result["open_positions"]["EURUSD"]
        self.assertEqual(rec_pos["direction"], "SELL")
        self.assertNotEqual(rec_pos["direction"], "ACTIVE")
        self.assertEqual(rec_pos["tickets"], [301, 302])

    def test_daily_risk_anchor_restoration_same_day(self):
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.manager.save_daily_risk(
            date_str=today_str,
            day_start_balance=5100.0,
            daily_halt_active=False,
            symbol_consecutive_losses={"XAUUSD": 1, "EURUSD": 2},
            symbol_daily_halted={"EURUSD": True}
        )

        # Before recovery, risk guardian defaults to baseline 5000
        self.assertEqual(self.risk_guardian.day_start_balance, 5000.0)

        result = self.manager.recover_state(
            live_positions=[],
            order_manager=self.order_mgr,
            risk_guardian=self.risk_guardian
        )

        self.assertTrue(result["risk_restored"])
        self.assertEqual(self.risk_guardian.day_start_balance, 5100.0)
        self.assertEqual(self.risk_guardian.symbol_consecutive_losses["EURUSD"], 2)
        self.assertTrue(self.risk_guardian.symbol_daily_halted["EURUSD"])

    def test_daily_risk_anchor_rollover_new_day(self):
        yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        self.manager.save_daily_risk(
            date_str=yesterday_str,
            day_start_balance=5100.0,
            daily_halt_active=True,
            symbol_consecutive_losses={"XAUUSD": 2},
            symbol_daily_halted={"XAUUSD": True}
        )

        result = self.manager.recover_state(
            live_positions=[],
            order_manager=self.order_mgr,
            risk_guardian=self.risk_guardian
        )

        # Risk state from yesterday should NOT restore yesterday's halt flags or losses
        self.assertFalse(result["risk_restored"])
        self.assertFalse(self.risk_guardian.daily_halt_active)
        self.assertEqual(self.risk_guardian.symbol_consecutive_losses.get("XAUUSD", 0), 0)

    def test_corrupted_state_recovery(self):
        # Write corrupted JSON
        with open(self.state_file, "w") as f:
            f.write("{invalid json content---")

        loaded = self.manager.load_state()
        self.assertEqual(loaded["open_positions"], {})
        self.assertIsNone(loaded["daily_risk_state"])

    def test_datetime_serialization_in_save_position(self):
        # Python datetime object passed directly into pos_data
        pos_data = {
            "group_id": "GRP_DT_1",
            "strategy": "APEXHUNTER_1_2_1",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "entry_price": 2650.0,
            "sl": 2640.0,
            "be_sl": 2652.0,
            "tp1_hit": False,
            "tickets": [501, 502],
            "entry_time": datetime.now(timezone.utc)  # Real datetime object
        }
        # Must not raise TypeError
        self.manager.save_position("GRP_DT_1", pos_data)
        loaded = self.manager.get_position("GRP_DT_1")
        self.assertIsNotNone(loaded)
        self.assertIn("entry_time", loaded)

    def test_tp1_hit_runner_not_yet_at_be_triggers_modification(self):
        # Order 1 closed offline, but runner 102 in MT5 still has initial SL at 2640.0
        pos_data = {
            "group_id": "GRP_XAU_RUNNER_UNPROTECTED",
            "strategy": "APEXHUNTER_1_2_1",
            "symbol": "XAUUSD",
            "broker_sym": "GOLD",
            "direction": "BUY",
            "entry_price": 2650.0,
            "sl": 2640.0,
            "be_sl": 2652.0,
            "tp1_hit": False,
            "tickets": [101, 102]
        }
        self.manager.save_position("GRP_XAU_RUNNER_UNPROTECTED", pos_data)

        # Runner position in MT5 still has initial SL (2640.0), NOT BE (2652.0)
        live_mt5_positions = [
            MockMT5Position(ticket=102, symbol="GOLD", type_code=0, magic=20260902, price_open=2650.0, sl=2640.0, tp=2680.0, volume=0.1, comment="APE_T2")
        ]

        result = self.manager.recover_state(
            live_positions=live_mt5_positions,
            order_manager=self.order_mgr,
            risk_guardian=self.risk_guardian
        )

        # Since runner is NOT yet at BE on MT5, be_updated must be False
        grp = self.order_mgr.active_groups["GRP_XAU_RUNNER_UNPROTECTED"]
        self.assertFalse(grp["be_updated"])
        self.assertNotIn(102, grp["modified_tickets"])

        # Order manager evaluate_breakeven_modifications must return ticket 102 for modification
        mods = self.order_mgr.evaluate_breakeven_modifications(
            open_ticket_ids=[102],
            closed_deal_tickets=[101]
        )
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0]["ticket"], 102)
        self.assertEqual(mods[0]["new_sl"], 2652.0)

    def test_relative_path_initialization(self):
        # Must not raise FileNotFoundError on relative path
        rel_mgr = BotPersistenceManager("test_rel_state.json")
        self.assertTrue(os.path.isabs(rel_mgr.state_file_path))
        if os.path.exists(rel_mgr.state_file_path):
            os.remove(rel_mgr.state_file_path)


if __name__ == "__main__":
    unittest.main()
