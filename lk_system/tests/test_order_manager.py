import unittest
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.order_manager import OrderManager

class TestOrderManager(unittest.TestCase):
    def setUp(self):
        self.mgr = OrderManager(magic_base=88000)

    def test_apexhunter_2_way_split(self):
        # ApexHunter: total_lots = 0.20, tp1 = 2704.2, tp2 = 2706.3
        splits = self.mgr.calculate_order_splits(
            strategy="APEXHUNTER_1_2_1",
            total_lots=0.20,
            tp_levels=[2704.2, 2706.3],
            volume_min=0.01,
            volume_step=0.01
        )
        self.assertEqual(len(splits), 2)
        self.assertEqual(splits[0]['volume'], 0.10)
        self.assertEqual(splits[0]['tp'], 2704.2)
        self.assertEqual(splits[1]['volume'], 0.10)
        self.assertEqual(splits[1]['tp'], 2706.3)
        self.assertAlmostEqual(splits[0]['volume'] + splits[1]['volume'], 0.20, places=2)

    def test_forex_3_way_split_normal_lots(self):
        # Forex SMC: total_lots = 0.33, tp1=2.5R, tp2=5.0R, tp3=8.0R
        splits = self.mgr.calculate_order_splits(
            strategy="LEWIS_KELLY_SMC",
            total_lots=0.33,
            tp_levels=[1.08875, 1.09250, 1.09700],
            volume_min=0.01,
            volume_step=0.01
        )
        self.assertEqual(len(splits), 3)
        # 30% / 30% / 40%
        self.assertEqual(splits[0]['volume'], 0.10) # 30%
        self.assertEqual(splits[0]['tp'], 1.08875)
        self.assertEqual(splits[1]['volume'], 0.10) # 30%
        self.assertEqual(splits[1]['tp'], 1.09250)
        self.assertEqual(splits[2]['volume'], 0.13) # 40%
        self.assertEqual(splits[2]['tp'], 1.09700)
        self.assertAlmostEqual(splits[0]['volume'] + splits[1]['volume'] + splits[2]['volume'], 0.33, places=2)

    def test_forex_micro_lot_fallbacks(self):
        # If total_lots = 0.02, split into 2 orders (0.01 @ 2.5R, 0.01 @ 8.0R)
        splits_2 = self.mgr.calculate_order_splits(
            strategy="LEWIS_KELLY_SMC",
            total_lots=0.02,
            tp_levels=[1.08875, 1.09250, 1.09700],
            volume_min=0.01,
            volume_step=0.01
        )
        self.assertEqual(len(splits_2), 2)
        self.assertEqual(splits_2[0]['volume'], 0.01)
        self.assertEqual(splits_2[0]['tp'], 1.08875)
        self.assertEqual(splits_2[1]['volume'], 0.01)
        self.assertEqual(splits_2[1]['tp'], 1.09700)

        # If total_lots = 0.01, single order
        splits_1 = self.mgr.calculate_order_splits(
            strategy="LEWIS_KELLY_SMC",
            total_lots=0.01,
            tp_levels=[1.08875, 1.09250, 1.09700],
            volume_min=0.01,
            volume_step=0.01
        )
        self.assertEqual(len(splits_1), 1)
        self.assertEqual(splits_1[0]['volume'], 0.01)
        self.assertEqual(splits_1[0]['tp'], 1.08875)

    def test_breakeven_retry_safety(self):
        # Register a group with tickets [101, 102, 103]
        self.mgr.register_position_group(
            group_id="EURUSD_TEST",
            strategy="LEWIS_KELLY_SMC",
            symbol="EURUSD",
            entry_price=1.08500,
            initial_sl=1.08250,
            be_sl=1.08550,
            tickets=[101, 102, 103]
        )

        # Ticket 101 closes at TP1. Tickets 102 and 103 are still open.
        mods = self.mgr.evaluate_breakeven_modifications(
            open_ticket_ids=[102, 103],
            closed_deal_tickets=[101]
        )
        self.assertEqual(len(mods), 2)
        self.assertEqual(mods[0]['ticket'], 102)
        self.assertEqual(mods[1]['ticket'], 103)

        # Suppose ticket 102 succeeded on MT5, but ticket 103 failed (requote)
        self.mgr.record_ticket_modified("EURUSD_TEST", 102)

        # Next cycle: only ticket 103 should be returned for modification
        mods_next = self.mgr.evaluate_breakeven_modifications(
            open_ticket_ids=[102, 103],
            closed_deal_tickets=[101]
        )
        self.assertEqual(len(mods_next), 1)
        self.assertEqual(mods_next[0]['ticket'], 103)

        # Once ticket 103 succeeds, no more modifications needed
        self.mgr.record_ticket_modified("EURUSD_TEST", 103)
        mods_final = self.mgr.evaluate_breakeven_modifications(
            open_ticket_ids=[102, 103],
            closed_deal_tickets=[101]
        )
        self.assertEqual(len(mods_final), 0)

    def test_recover_from_mt5(self):
        from collections import namedtuple
        MockPos = namedtuple('MockPos', ['ticket', 'symbol', 'magic', 'type', 'price_open', 'sl', 'tp'])
        
        # 2 orders open for EURUSD
        positions = [
            MockPos(ticket=201, symbol="EURUSD", magic=88001, type=0, price_open=1.0850, sl=1.0820, tp=1.0900),
            MockPos(ticket=202, symbol="EURUSD", magic=88002, type=0, price_open=1.0850, sl=1.0820, tp=1.0950),
            # Unrelated manual trade
            MockPos(ticket=999, symbol="USDCHF", magic=0, type=0, price_open=0.9000, sl=0.8950, tp=0.9100),
        ]
        count = self.mgr.recover_from_mt5(positions)
        self.assertEqual(count, 1)
        self.assertIn("RECOVERED_EURUSD_201", self.mgr.active_groups)
        grp = self.mgr.active_groups["RECOVERED_EURUSD_201"]
        self.assertEqual(grp['tickets'], [201, 202])
        self.assertEqual(grp['symbol'], "EURUSD")

    def test_price_stops_level_validation(self):
        # SELL trade: price = 1.09762, stops_level = 10 pts (0.00010 point)
        # Broker requires Ask + stops_level * point <= SL (i.e. SL must be at least 1.09772)
        # If new_sl is 1.09758 (below price or within stops_level), it must be REJECTED as invalid stops!
        valid_sell = OrderManager.check_price_stops_distance(
            order_type=1, # SELL
            current_price=1.09762,
            target_sl=1.09758,
            stops_level_pts=10,
            point=0.00001
        )
        self.assertFalse(valid_sell, "SELL with SL below market or within stops_level must be rejected before MT5")

        # Valid SELL: SL well above market
        valid_sell_ok = OrderManager.check_price_stops_distance(
            order_type=1,
            current_price=1.09740,
            target_sl=1.09758,
            stops_level_pts=10,
            point=0.00001
        )
        self.assertTrue(valid_sell_ok)

        # BUY trade: price = 1.09750, stops_level = 10 pts
        # Broker requires Bid - stops_level * point >= SL (i.e. SL must be at most 1.09740)
        valid_buy = OrderManager.check_price_stops_distance(
            order_type=0, # BUY
            current_price=1.09750,
            target_sl=1.09745,
            stops_level_pts=10,
            point=0.00001
        )
        self.assertFalse(valid_buy, "BUY with SL too close to market or above market must be rejected before MT5")

        valid_buy_ok = OrderManager.check_price_stops_distance(
            order_type=0,
            current_price=1.09760,
            target_sl=1.09740,
            stops_level_pts=10,
            point=0.00001
        )
        self.assertTrue(valid_buy_ok)

    def test_modification_cooldown(self):
        # Test ticket modification cooldown (prevent hammering MT5 every second)
        ticket = 5731212961
        now_ts = 1000.0
        # First attempt is allowed
        self.assertTrue(self.mgr.can_attempt_modification(ticket, current_time=now_ts, cooldown_sec=30.0))
        self.mgr.record_modification_attempt(ticket, timestamp=now_ts)

        # 5 seconds later: should be blocked by cooldown
        self.assertFalse(self.mgr.can_attempt_modification(ticket, current_time=now_ts + 5.0, cooldown_sec=30.0))

        # 31 seconds later: should be allowed again
        self.assertTrue(self.mgr.can_attempt_modification(ticket, current_time=now_ts + 31.0, cooldown_sec=30.0))

if __name__ == '__main__':
    unittest.main()
