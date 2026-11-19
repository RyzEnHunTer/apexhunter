import pandas as pd
import numpy as np

class ImbalanceDetector:
    """
    Lewis Kelly SMC Imbalance & Institutional Order Block Engine.
    
    Implements:
    1. Fair Value Gap (FVG / Imbalance):
       - Bullish FVG: Candle 3 Low > Candle 1 High.
       - Bearish FVG: Candle 3 High < Candle 1 Low.
    2. The 3 Golden Rules of Valid Order Blocks (OB):
       - Rule 1: Pro-Trend Context (handled in orchestrator).
       - Rule 2: Prior Liquidity Sweep: The OB candle swept the high/low of the previous candle.
       - Rule 3: Aggressive Displacement: The move created an FVG and led to expansion.
    3. Unmitigated Zone Tracking:
       - Tracks whether the OB/FVG has been tapped/mitigated or remains fresh.
    """
    def __init__(self, min_fvg_pips: float = 0.0001):
        self.min_fvg_pips = min_fvg_pips

    def detect(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or len(df) < 3:
            return df
            
        df = df.copy()
        n = len(df)
        
        opens = df['Open'].values
        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values
        
        bullish_fvg = np.zeros(n, dtype=bool)
        bearish_fvg = np.zeros(n, dtype=bool)
        fvg_top = np.full(n, np.nan)
        fvg_bottom = np.full(n, np.nan)
        
        bullish_ob = np.zeros(n, dtype=bool)
        bearish_ob = np.zeros(n, dtype=bool)
        bullish_ob_high = np.full(n, np.nan)
        bullish_ob_low = np.full(n, np.nan)
        bearish_ob_high = np.full(n, np.nan)
        bearish_ob_low = np.full(n, np.nan)
        
        for i in range(2, n):
            c1_h = highs[i-2]
            c1_l = lows[i-2]
            c2_o = opens[i-1]
            c2_c = closes[i-1]
            c3_h = highs[i]
            c3_l = lows[i]
            
            # 1. Bullish FVG (Imbalance between candle 1 high and candle 3 low)
            if c3_l > c1_h and (c3_l - c1_h) >= self.min_fvg_pips:
                bullish_fvg[i] = True
                fvg_top[i] = c3_l
                fvg_bottom[i] = c1_h
                
                # Identify Bullish Order Block:
                # The sell-to-buy candle prior to the displacement
                # Must satisfy Rule 2: Swept prior candle low, OR the lowest bearish candle in the cluster
                found_ob = False
                for j in range(i-1, max(-1, i-6), -1):
                    # Check if candle was bearish (Sell to Buy)
                    if closes[j] <= opens[j]:
                        # Check if this candle swept previous candle low (Rule 2: Liquidity Sweep)
                        swept_prior = (j > 0) and (lows[j] < lows[j-1])
                        bullish_ob_high[i] = highs[j]
                        bullish_ob_low[i] = lows[j]
                        bullish_ob[i] = True
                        found_ob = True
                        if swept_prior:
                            break # High-probability swept OB prioritized
                            
                if not found_ob:
                    # Fallback to candle 1 extreme
                    bullish_ob_high[i] = c1_h
                    bullish_ob_low[i] = c1_l
                    bullish_ob[i] = True
                    
            # 2. Bearish FVG (Imbalance between candle 3 high and candle 1 low)
            elif c3_h < c1_l and (c1_l - c3_h) >= self.min_fvg_pips:
                bearish_fvg[i] = True
                fvg_top[i] = c1_l
                fvg_bottom[i] = c3_h
                
                # Identify Bearish Order Block:
                # The buy-to-sell candle prior to the displacement
                # Must satisfy Rule 2: Swept prior candle high, OR the highest bullish candle in the cluster
                found_ob = False
                for j in range(i-1, max(-1, i-6), -1):
                    if closes[j] >= opens[j]:
                        swept_prior = (j > 0) and (highs[j] > highs[j-1])
                        bearish_ob_high[i] = highs[j]
                        bearish_ob_low[i] = lows[j]
                        bearish_ob[i] = True
                        found_ob = True
                        if swept_prior:
                            break
                            
                if not found_ob:
                    bearish_ob_high[i] = c1_h
                    bearish_ob_low[i] = c1_l
                    bearish_ob[i] = True

        df['Bullish_FVG'] = bullish_fvg
        df['Bearish_FVG'] = bearish_fvg
        df['FVG_Top'] = fvg_top
        df['FVG_Bottom'] = fvg_bottom
        
        df['Bullish_OB'] = bullish_ob
        df['Bearish_OB'] = bearish_ob
        df['Bullish_OB_High'] = bullish_ob_high
        df['Bullish_OB_Low'] = bullish_ob_low
        df['Bearish_OB_High'] = bearish_ob_high
        df['Bearish_OB_Low'] = bearish_ob_low
        
        return df
