"""
Local DCC Engine v1.2 Export Module
====================================
Location: dcc_engine_v1_2.py

Provides direct local access to the self-contained ApexHunter engine
without any external directory dependencies.
"""

from core.apexhunter_engine import (
    DCCEngineV12,
    SignalType,
    TradeSignalV12,
)

__all__ = ["DCCEngineV12", "SignalType", "TradeSignalV12"]
