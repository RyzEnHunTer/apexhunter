"""
Spread Guardian Module - Production Grade
=========================================
Implements the calibrated Golden Numbers spread filter:
1. Absolute Max Spread Caps (protects against bank rollovers and illiquid off-hours):
   - EURUSD: 2.50 pips
   - GBPUSD: 3.00 pips
   - AUDUSD: 3.00 pips
   - USDJPY: 3.50 pips
   - USDCAD: 3.20 pips
   - EURJPY: 3.80 pips
   - XAUUSD: $1.00
   - NAS100: 6.50 pts
2. Relative Spread-to-SL Ratio:
   - Enforces that spread cannot exceed 30.0% of the trade's Stop Loss distance.
   - Prevents starting tight setups at an insurmountable statistical disadvantage.
"""

from typing import Tuple, Dict, Optional

class SpreadGuardian:
    DEFAULT_CAPS: Dict[str, float] = {
        'EURUSD': 2.50,
        'GBPUSD': 3.00,
        'AUDUSD': 3.00,
        'USDJPY': 3.50,
        'USDCAD': 3.20,
        'EURJPY': 3.80,
        'XAUUSD': 1.00,
        'GOLD': 1.00,
        'NAS100': 6.50,
        'USTEC': 6.50,
    }

    def __init__(
        self,
        max_spread_sl_ratio: float = 0.30, # Max 30% of SL distance
        custom_caps: Optional[Dict[str, float]] = None
    ):
        self.max_spread_sl_ratio = max_spread_sl_ratio
        self.caps = self.DEFAULT_CAPS.copy()
        if custom_caps:
            self.caps.update(custom_caps)

    def clean_symbol(self, symbol: str) -> str:
        """Removes broker suffixes like .r, .m, / etc."""
        return symbol.replace('.r', '').replace('.m', '').replace('/', '').upper()

    def get_pip_multiplier(self, clean_symbol: str) -> float:
        """Returns the multiplier to convert raw price spread to pips/pts."""
        if 'JPY' in clean_symbol:
            return 100.0       # 0.01 pip
        elif 'XAU' in clean_symbol or 'GOLD' in clean_symbol:
            return 1.0         # 1.0 = $1.00
        elif 'NAS' in clean_symbol or 'USTEC' in clean_symbol or 'US30' in clean_symbol:
            return 1.0         # 1.0 = 1.0 pt
        else:
            return 10000.0     # 0.0001 pip (EURUSD, GBPUSD, etc.)

    def is_spread_acceptable(
        self,
        symbol: str,
        live_ask: float,
        live_bid: float,
        entry_price: float,
        sl_price: float
    ) -> Tuple[bool, str]:
        """
        Evaluates live spread against absolute caps and relative SL ratio.
        """
        raw_spread = live_ask - live_bid
        if raw_spread < 0:
            return False, f"Invalid negative spread detected ({raw_spread})"

        clean_sym = self.clean_symbol(symbol)
        mult = self.get_pip_multiplier(clean_sym)
        spread_units = raw_spread * mult

        # 1. Absolute Cap Check
        max_cap = self.caps.get(clean_sym, 5.0) # default fallback
        if spread_units > max_cap:
            unit_name = "$" if "XAU" in clean_sym else ("pts" if "NAS" in clean_sym else "pips")
            return False, f"Spread {spread_units:.2f} {unit_name} exceeds absolute max cap {max_cap:.2f} {unit_name} (Market Illiquid/Rollover)"

        # 2. Relative Spread-to-SL Ratio Check (Max 30%)
        sl_distance = abs(entry_price - sl_price)
        if sl_distance > 0:
            ratio = raw_spread / sl_distance
            if ratio > self.max_spread_sl_ratio:
                return False, f"Spread ({spread_units:.2f}) consumes {ratio*100:.1f}% of SL distance (exceeds {self.max_spread_sl_ratio*100:.1f}% limit)"

        return True, f"Spread OK ({spread_units:.2f})"
