import pandas as pd
import numpy as np
from .market_structure import MarketStructure

class DXYEngine:
    """
    Constructs a real-time synthetic US Dollar Index (DXY) from primary major Forex pairs
    and computes its market structure (Trend, CHoCH, BoS) to provide macroeconomic
    inverse-correlation confirmation for EURUSD trades, as demonstrated by Lewis Kelly.
    """
    def __init__(self):
        self.market_structure = MarketStructure()
        # Official ICE DXY Geometric Weighting (normalized for available liquid majors)
        self.k = 50.14348112
        self.w_eur = -0.576
        self.w_jpy = 0.136
        self.w_gbp = -0.119
        
    def synthesize_dxy(self, df_eur, df_gbp, df_jpy):
        """
        Synthesizes an OHLC DataFrame for DXY from EURUSD, GBPUSD, and USDJPY.
        """
        # Align timestamps across all 3 majors
        common_idx = df_eur.index.intersection(df_gbp.index).intersection(df_jpy.index)
        if len(common_idx) == 0:
            return pd.DataFrame()
            
        eur = df_eur.loc[common_idx]
        gbp = df_gbp.loc[common_idx]
        jpy = df_jpy.loc[common_idx]
        
        # Calculate OHLC for DXY
        dxy_open = self.k * (eur['Open']**self.w_eur) * (jpy['Open']**self.w_jpy) * (gbp['Open']**self.w_gbp)
        dxy_close = self.k * (eur['Close']**self.w_eur) * (jpy['Close']**self.w_jpy) * (gbp['Close']**self.w_gbp)
        
        # Inversion of EUR/GBP high/low
        dxy_high = self.k * (eur['Low']**self.w_eur) * (jpy['High']**self.w_jpy) * (gbp['Low']**self.w_gbp)
        dxy_low = self.k * (eur['High']**self.w_eur) * (jpy['Low']**self.w_jpy) * (gbp['High']**self.w_gbp)
        
        df_dxy = pd.DataFrame({
            'Open': dxy_open,
            'High': dxy_high,
            'Low': dxy_low,
            'Close': dxy_close
        }, index=common_idx)
        
        # Compute DXY Market Structure
        df_dxy = self.market_structure.update(df_dxy)
        return df_dxy

    def is_aligned(self, eur_bias, dxy_trend):
        """
        Returns True if DXY structure inversely confirms EURUSD bias:
        - EURUSD Long requires DXY Bearish (or non-bullish)
        - EURUSD Short requires DXY Bullish (or non-bearish)
        """
        if eur_bias == 'bullish':
            return dxy_trend == 'bearish'
        elif eur_bias == 'bearish':
            return dxy_trend == 'bullish'
        return True
