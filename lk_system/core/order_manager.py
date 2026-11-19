"""
Order Manager Module - Production Grade
=======================================
Implements the Server-Side Split-Order Execution & Breakeven Engine:
1. ApexHunter (Gold & Nasdaq):
   - 2 Split Orders (50% lots @ 1.40R TP, 50% lots @ 2.10R TP).
   - Server-side hard TP on both tickets.
   - When Order 1 hits TP1, Order 2 is modified to Breakeven (entry + spread).
2. Lewis Kelly Forex SMC:
   - 3 Split Orders (30% lots @ 2.50R, 30% lots @ 5.00R, 40% lots @ 8.00R).
   - Micro-lot adaptive fallbacks (2 orders if 0.02 lots, 1 order if 0.01 lots).
   - When Order 1 hits TP1, Orders 2 & 3 are modified to Breakeven (+0.2R).
3. Group & Ticket Tracking:
   - Maintains order group metadata for fail-safe crash recovery and status reporting.
"""

from typing import List, Dict, Any, Optional
import math

class OrderManager:
    def __init__(self, magic_base: int = 88000):
        self.magic_base = magic_base
        # Map: group_id -> {strategy, symbol, entry_p, initial_sl, be_sl, tickets: [t1, t2, t3], be_updated: bool}
        self.active_groups: Dict[str, Dict[str, Any]] = {}

    def calculate_order_splits(
        self,
        strategy: str,
        total_lots: float,
        tp_levels: List[float],
        volume_min: float = 0.01,
        volume_step: float = 0.01
    ) -> List[Dict[str, Any]]:
        """
        Splits total volume into dedicated server-side order targets.
        """
        total_lots = round(round(total_lots / volume_step) * volume_step, 2)
        if total_lots < volume_min:
            total_lots = volume_min

        splits = []

        if strategy == "APEXHUNTER_1_2_1":
            # 2-Way Split (50% / 50%)
            if total_lots < (volume_min * 2):
                # Cannot split below 0.02 lots
                return [{
                    'tier': 1,
                    'volume': total_lots,
                    'tp': tp_levels[0] if len(tp_levels) > 0 else 0.0,
                    'label': 'Apex_Single'
                }]

            vol_1 = round(round((total_lots * 0.50) / volume_step) * volume_step, 2)
            vol_2 = round(total_lots - vol_1, 2)
            
            # Ensure neither is below min
            if vol_1 < volume_min or vol_2 < volume_min:
                vol_1 = volume_min
                vol_2 = round(total_lots - vol_1, 2)

            splits = [
                {'tier': 1, 'volume': vol_1, 'tp': tp_levels[0], 'label': 'Apex_TP1_1.4R'},
                {'tier': 2, 'volume': vol_2, 'tp': tp_levels[1] if len(tp_levels) > 1 else tp_levels[0], 'label': 'Apex_TP2_2.1R'}
            ]

        else: # Lewis Kelly Forex SMC (3-Way Split: 30% / 30% / 40%)
            if total_lots < (volume_min * 2):
                # 0.01 lots -> Single order @ 2.5R
                return [{
                    'tier': 1,
                    'volume': total_lots,
                    'tp': tp_levels[0],
                    'label': 'SMC_Single_2.5R'
                }]
            elif total_lots < (volume_min * 3):
                # 0.02 lots -> 2 orders (50% @ 2.5R, 50% @ 8.0R)
                tp_runner = tp_levels[2] if len(tp_levels) >= 3 else (tp_levels[1] if len(tp_levels) >= 2 else tp_levels[0])
                return [
                    {'tier': 1, 'volume': volume_min, 'tp': tp_levels[0], 'label': 'SMC_TP1_2.5R'},
                    {'tier': 2, 'volume': volume_min, 'tp': tp_runner, 'label': 'SMC_TP3_8.0R'}
                ]
            else:
                # 3-Way Split (30% / 30% / 40%)
                vol_1 = round(round((total_lots * 0.30) / volume_step) * volume_step, 2)
                vol_2 = round(round((total_lots * 0.30) / volume_step) * volume_step, 2)
                vol_3 = round(total_lots - vol_1 - vol_2, 2)

                # Ensure minimum compliance
                vol_1 = max(volume_min, vol_1)
                vol_2 = max(volume_min, vol_2)
                vol_3 = max(volume_min, vol_3)
                # Re-adjust rounding difference
                diff = round(total_lots - (vol_1 + vol_2 + vol_3), 2)
                vol_3 = round(vol_3 + diff, 2)

                splits = [
                    {'tier': 1, 'volume': vol_1, 'tp': tp_levels[0], 'label': 'SMC_TP1_2.5R'},
                    {'tier': 2, 'volume': vol_2, 'tp': tp_levels[1] if len(tp_levels) > 1 else tp_levels[0], 'label': 'SMC_TP2_5.0R'},
                    {'tier': 3, 'volume': vol_3, 'tp': tp_levels[2] if len(tp_levels) > 2 else tp_levels[0], 'label': 'SMC_TP3_8.0R'}
                ]

        return splits

    def register_position_group(
        self,
        group_id: str,
        strategy: str,
        symbol: str,
        entry_price: float,
        initial_sl: float,
        be_sl: float,
        tickets: List[int]
    ):
        """Registers a group of split order tickets under a single trade lifecycle."""
        self.active_groups[group_id] = {
            'strategy': strategy,
            'symbol': symbol,
            'entry_price': entry_price,
            'initial_sl': initial_sl,
            'be_sl': be_sl,
            'tickets': tickets,
            'modified_tickets': set(),
            'be_updated': False
        }

    def record_ticket_modified(self, group_id: str, ticket: int):
        """Records that a sibling ticket has been successfully updated to Breakeven on MT5."""
        if group_id in self.active_groups:
            grp = self.active_groups[group_id]
            grp['modified_tickets'].add(ticket)
            # If all sibling tickets (tickets[1:]) have been modified, mark group as fully updated
            sibling_tickets = set(grp['tickets'][1:])
            if sibling_tickets and sibling_tickets.issubset(grp['modified_tickets']):
                grp['be_updated'] = True

    def evaluate_breakeven_modifications(
        self,
        open_ticket_ids: List[int],
        closed_deal_tickets: List[int]
    ) -> List[Dict[str, Any]]:
        """
        Detects if Order 1 has closed (hit TP1) while sibling orders remain open.
        Only returns modification tasks for sibling tickets that have NOT yet succeeded.
        """
        modifications = []

        for group_id, grp in list(self.active_groups.items()):
            if grp['be_updated']:
                continue

            tickets = grp['tickets']
            if len(tickets) <= 1:
                grp['be_updated'] = True
                continue

            ticket_1 = tickets[0]
            # Has Order 1 closed? (either in closed_deal_tickets or missing from open_ticket_ids)
            order_1_closed = (ticket_1 in closed_deal_tickets) or (ticket_1 not in open_ticket_ids)

            if order_1_closed:
                # Sibling tickets still open on MT5 and not yet confirmed modified
                sibling_tickets = [
                    t for t in tickets[1:]
                    if t in open_ticket_ids and t not in grp.get('modified_tickets', set())
                ]
                if sibling_tickets:
                    new_sl = grp['be_sl']
                    for sib_t in sibling_tickets:
                        modifications.append({
                            'group_id': group_id,
                            'ticket': sib_t,
                            'symbol': grp['symbol'],
                            'new_sl': new_sl
                        })
                else:
                    # No remaining siblings need modification
                    grp['be_updated'] = True

        return modifications

    def recover_from_mt5(self, positions: List[Any]) -> int:
        """
        Reconstructs active position groups on bot startup from live MT5 positions.
        Returns the number of recovered groups.
        """
        if not positions:
            return 0

        # Group positions by symbol where magic falls within bot range [magic_base+1 ... magic_base+9]
        valid_magics = set(range(self.magic_base + 1, self.magic_base + 10))
        grouped: Dict[str, List[Any]] = {}
        for p in positions:
            if p.magic in valid_magics:
                sym = p.symbol
                grouped.setdefault(sym, []).append(p)

        recovered_count = 0
        for sym, pos_list in grouped.items():
            pos_list.sort(key=lambda x: x.magic)
            tickets = [p.ticket for p in pos_list]
            p0 = pos_list[0]
            strategy = "APEXHUNTER_1_2_1" if any(s in sym.upper() for s in ['XAU', 'GOLD', 'NAS', 'USTEC']) else "LEWIS_KELLY_SMC"
            group_id = f"RECOVERED_{sym}_{p0.ticket}"
            entry_p = p0.price_open
            sl_p = p0.sl
            sl_dist = abs(entry_p - sl_p)
            be_sl = entry_p + (0.2 * sl_dist) if p0.type == 0 else entry_p - (0.2 * sl_dist)

            self.register_position_group(
                group_id=group_id,
                strategy=strategy,
                symbol=sym,
                entry_price=entry_p,
                initial_sl=sl_p,
                be_sl=be_sl,
                tickets=tickets
            )
            recovered_count += 1

        return recovered_count
