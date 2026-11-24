"""
Dynamic Asset Handler & Broker Symbol Resolver
==============================================
Location: core/asset_handler.py

Provides intelligent, multi-stage asset resolution and specification caching across
all MetaTrader 5 brokers and prop firms (FTMO, FundedNext, Alpha Capital, Tickmill, IC Markets, etc.).

Resolution Pipeline:
1. Direct Exact & Case-Insensitive Matching
2. Canonical Multi-Alias Dictionary Matching
3. Dynamic Prefix & Suffix Stripping / Normalization
4. Fuzzy Semantic Discovery (XAU/GOLD and NAS/USTEC/US100/NDX/NQ/TECH)
5. Graceful Degradation: Marks unavailable assets as DISABLED_BY_BROKER without halting the bot
6. Automatic MT5 Market Watch selection and specification caching
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    mt5 = None


@dataclass
class SymbolMatchResult:
    canonical: str
    broker_symbol: Optional[str] = None
    status: str = "DISABLED_BY_BROKER"  # "ACTIVE" or "DISABLED_BY_BROKER"
    specs: Dict[str, Any] = field(default_factory=dict)


class AssetHandler:
    """
    Intelligent Asset Resolution and Market Watch Management for MetaTrader 5.
    """

    DEFAULT_CANONICAL_TARGETS = [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "USDCAD",
        "XAUUSD", "NAS100"
    ]

    CANONICAL_ALIASES: Dict[str, List[str]] = {
        "XAUUSD": [
            "XAUUSD", "GOLD", "XAUUSD.m", "XAUUSD.pro", "XAUUSD.raw", "XAUUSD.ecn",
            "XAUUSD+", "XAUUSD_i", "XAUUSD.c", "XAUUSD.a", "XAUUSD.s", "XAUUSD.std",
            "XAUUSD_sb", "GOLD.pro", "GOLD.m", "GOLD.raw", "GOLDcash", "XAU/USD",
            "XAUUSD-", "XAUUSD_", "XAUUSDb", "GOLD.ecn", "GOLD+"
        ],
        "NAS100": [
            "NAS100", "USTEC", "US100", "NDX", "NQ", "NAS100.cash", "USTEC.cash",
            "US100.cash", "NDX100", "NAS100m", "NAS100.m", "NAS100.pro", "NAS100.raw",
            "NAS100.ecn", "NAS100+", "USTEC.m", "USTEC.pro", "USTEC.raw", "US100.m",
            "NQ100", "USA100", "USATECH", "US-TECH100", "NAS100Cash", "USTECCash",
            "NAS100_sb", "NAS100.c", "US100Cash", "TECH100", "TECH100.cash"
        ],
        "EURUSD": ["EURUSD", "EURUSD.m", "EURUSD.raw", "EURUSD.pro", "EURUSD+", "EURUSD_i", "EURUSD.ecn", "EUR/USD", "pro.EURUSD", "ecn_EURUSD"],
        "GBPUSD": ["GBPUSD", "GBPUSD.m", "GBPUSD.raw", "GBPUSD.pro", "GBPUSD+", "GBPUSD_i", "GBPUSD.ecn", "GBP/USD", "pro.GBPUSD", "ecn_GBPUSD"],
        "USDJPY": ["USDJPY", "USDJPY.m", "USDJPY.raw", "USDJPY.pro", "USDJPY+", "USDJPY_i", "USDJPY.ecn", "USD/JPY", "pro.USDJPY", "r_USDJPY"],
        "AUDUSD": ["AUDUSD", "AUDUSD.m", "AUDUSD.raw", "AUDUSD.pro", "AUDUSD+", "AUDUSD_i", "AUDUSD.ecn", "AUD/USD", "pro.AUDUSD", "ecn_AUDUSD"],
        "EURJPY": ["EURJPY", "EURJPY.m", "EURJPY.raw", "EURJPY.pro", "EURJPY+", "EURJPY_i", "EURJPY.ecn", "EUR/JPY", "pro.EURJPY", "ecn_EURJPY"],
        "USDCAD": ["USDCAD", "USDCAD.m", "USDCAD.raw", "USDCAD.pro", "USDCAD+", "USDCAD_i", "USDCAD.ecn", "USD/CAD", "pro.USDCAD", "ecn_USDCAD"],
    }

    # Common prefixes used by retail & prop brokers
    PREFIX_PATTERNS = [
        r"^pro\.", r"^ecn\.", r"^ecn_", r"^std\.", r"^std_", r"^r_", r"^r\.",
        r"^raw\.", r"^raw_", r"^sb\.", r"^sb_", r"^m\.", r"^i\."
    ]

    # Common suffixes used by retail & prop brokers
    SUFFIX_PATTERNS = [
        r"\.m$", r"\.raw$", r"\.pro$", r"\.ecn$", r"\.std$", r"\.c$", r"\.a$",
        r"\.s$", r"\+$", r"_i$", r"_sb$", r"\.cash$", r"cash$", r"Cash$",
        r"_spot_usd$", r"_spot$", r"_b$", r"\.e$", r"\.i$", r"\.sb$"
    ]

    def __init__(self, canonical_targets: Optional[List[str]] = None, logger: Optional[logging.Logger] = None):
        self.canonical_targets = canonical_targets or list(self.DEFAULT_CANONICAL_TARGETS)
        self.logger = logger or logging.getLogger("AssetHandler")
        self.resolved_symbols: Dict[str, SymbolMatchResult] = {}
        self.broker_to_canonical: Dict[str, str] = {}
        self.symbol_specs: Dict[str, Dict[str, Any]] = {}

    def _strip_affixes(self, name: str) -> str:
        """Strips known broker prefixes and suffixes to find root symbol."""
        cleaned = name.strip()
        for pat in self.PREFIX_PATTERNS:
            cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
        for pat in self.SUFFIX_PATTERNS:
            cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
        # Remove common delimiters
        cleaned = cleaned.replace("/", "").replace(".", "").replace("_", "").replace("-", "")
        return cleaned.upper()

    def _is_gold(self, sym: str) -> bool:
        """Determines if a broker symbol refers to Gold (XAUUSD)."""
        upper = sym.upper()
        if "XAU" not in upper and "GOLD" not in upper:
            return False
        # Avoid mapping XAUEUR / XAUAUD / XAUGBP / XAUCHF / XAUJPY to XAUUSD
        non_usd_fiats = ("EUR", "AUD", "GBP", "CHF", "JPY", "NZD", "CAD")
        if any(f in upper for f in non_usd_fiats) and "USD" not in upper:
            return False
        return True

    def _is_nasdaq(self, sym: str) -> bool:
        """Determines if a broker symbol refers to Nasdaq 100."""
        upper = sym.upper()
        exact_or_prefix = ["NAS100", "USTEC", "US100", "NDX", "USA100", "TECH100"]
        if any(r in upper for r in exact_or_prefix):
            return True
        return bool(re.search(r'(^|[\._\-])(NQ|TECH)(\d+|[\._\-]|CASH|$)', upper))

    def resolve_symbol(self, canonical: str, available_symbols: List[str]) -> Optional[str]:
        """
        Attempts to resolve a single canonical symbol against a list of broker symbols.
        Applies multi-stage matching logic.
        """
        if not available_symbols:
            return None

        sym_map = {s: s.upper() for s in available_symbols}

        # Stage 1: Direct exact match (case-insensitive)
        for original, upper in sym_map.items():
            if upper == canonical.upper():
                return original

        # Stage 2: Canonical aliases lookup
        aliases = self.CANONICAL_ALIASES.get(canonical, [canonical])
        alias_upper_set = {a.upper(): a for a in aliases}
        for original, upper in sym_map.items():
            if upper in alias_upper_set:
                return original

        # Stage 3: Prefix / Suffix stripped root matching
        # Match normalized alias or canonical root
        target_roots = {self._strip_affixes(a) for a in aliases}
        target_roots.add(self._strip_affixes(canonical))

        # Check exact stripped match
        for original, upper in sym_map.items():
            stripped = self._strip_affixes(original)
            if stripped in target_roots:
                return original

        # Stage 4: Fuzzy semantic discovery
        if canonical == "XAUUSD":
            # Priority 1: XAU or GOLD with USD/spot
            for original, upper in sym_map.items():
                if self._is_gold(original):
                    return original
        elif canonical == "NAS100":
            for original, upper in sym_map.items():
                if self._is_nasdaq(original):
                    return original
        else:
            # Forex pairs: check if the 6-letter currency root is cleanly contained
            forex_root = canonical.upper()
            for original, upper in sym_map.items():
                stripped = original.upper().replace("/", "").replace(".", "").replace("_", "").replace("-", "")
                if forex_root in stripped:
                    return original

        return None

    def resolve_all_symbols(self, available_broker_symbols: Optional[List[str]] = None) -> Dict[str, SymbolMatchResult]:
        """
        Resolves all configured canonical symbols against available broker symbols.
        If available_broker_symbols is None, fetches live symbols from MetaTrader 5.
        """
        if available_broker_symbols is not None:
            broker_syms = list(available_broker_symbols)
        else:
            if MT5_AVAILABLE and mt5 is not None:
                try:
                    all_syms = mt5.symbols_get()
                    broker_syms = [s.name for s in all_syms] if all_syms else []
                except Exception as e:
                    self.logger.warning(f"Could not retrieve symbols from MT5: {e}")
                    broker_syms = []
            else:
                broker_syms = []

        self.resolved_symbols = {}
        self.broker_to_canonical = {}

        for canon in self.canonical_targets:
            matched_sym = self.resolve_symbol(canon, broker_syms)

            if matched_sym:
                # If connected to MT5, select the symbol in Market Watch and cache specs
                specs = {}
                if MT5_AVAILABLE and mt5 is not None and available_broker_symbols is None:
                    try:
                        mt5.symbol_select(matched_sym, True)
                        info = mt5.symbol_info(matched_sym)
                        if info:
                            specs = {
                                "digits": getattr(info, "digits", 5),
                                "point": getattr(info, "point", 0.00001),
                                "tick_size": getattr(info, "trade_tick_size", getattr(info, "point", 0.00001)),
                                "tick_value": getattr(info, "trade_tick_value", 1.0),
                                "contract_size": getattr(info, "trade_contract_size", 100000.0),
                                "volume_min": getattr(info, "volume_min", 0.01),
                                "volume_step": getattr(info, "volume_step", 0.01),
                                "volume_max": getattr(info, "volume_max", 100.0),
                                "spread": getattr(info, "spread", 0),
                            }
                    except Exception as e:
                        self.logger.warning(f"Failed to query specs for {matched_sym}: {e}")

                res = SymbolMatchResult(
                    canonical=canon,
                    broker_symbol=matched_sym,
                    status="ACTIVE",
                    specs=specs
                )
                self.resolved_symbols[canon] = res
                self.broker_to_canonical[matched_sym] = canon
                self.symbol_specs[canon] = specs
                self.logger.info(f"[AssetHandler] Mapped '{canon}' -> Broker Symbol '{matched_sym}' (ACTIVE)")
            else:
                res = SymbolMatchResult(
                    canonical=canon,
                    broker_symbol=None,
                    status="DISABLED_BY_BROKER",
                    specs={}
                )
                self.resolved_symbols[canon] = res
                self.logger.warning(f"[AssetHandler] Asset '{canon}' not available on broker -> DISABLED_BY_BROKER")

        return self.resolved_symbols

    def is_symbol_tradeable(self, canonical: str) -> bool:
        """Returns True if the asset was resolved and is active on this broker."""
        res = self.resolved_symbols.get(canonical)
        return res is not None and res.status == "ACTIVE" and res.broker_symbol is not None

    def get_broker_symbol(self, canonical: str) -> Optional[str]:
        """Returns the broker's actual trading symbol name for a canonical target."""
        res = self.resolved_symbols.get(canonical)
        return res.broker_symbol if res else None

    def get_canonical_symbol(self, broker_sym: str) -> Optional[str]:
        """Translates a broker symbol name back to its canonical symbol."""
        if broker_sym in self.broker_to_canonical:
            return self.broker_to_canonical[broker_sym]
        for canon, res in self.resolved_symbols.items():
            if res.broker_symbol == broker_sym:
                return canon
        return None

    def get_specs(self, canonical: str) -> Dict[str, Any]:
        """Returns cached specifications for canonical symbol."""
        return self.symbol_specs.get(canonical, {})
