import pandas as pd
import numpy as np

class DealingRangeEngine:
    """
    Enforces Lewis Kelly's mandatory wholesale/retail pricing rule:
    - Smart Money sells at PREMIUM (above 50% Equilibrium)
    - Smart Money buys at DISCOUNT (below 50% Equilibrium)
    
    Optimal Trade Entry (OTE) is defined as the 61.8% to 78.6% Fibonacci retracement region.
    """
    def __init__(self):
        pass
        
    def calculate_range(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates Dealing Range levels, Equilibrium (50%), and OTE boundaries.
        Expects df to contain 'Trend', 'Strong_High', 'Strong_Low', 'Weak_High', 'Weak_Low'.
        """
        df = df.copy()
        n = len(df)
        if n == 0 or 'Trend' not in df.columns:
            df['Equilibrium'] = np.nan
            df['Fib_618'] = np.nan
            df['Fib_786'] = np.nan
            df['In_Premium'] = np.zeros(n, dtype=bool)
            df['In_Discount'] = np.zeros(n, dtype=bool)
            df['In_OTE'] = np.zeros(n, dtype=bool)
            return df
        
        equilibrium = np.full(n, np.nan)
        fib_618 = np.full(n, np.nan)
        fib_786 = np.full(n, np.nan)
        is_premium = np.zeros(n, dtype=bool)
        is_discount = np.zeros(n, dtype=bool)
        is_ote = np.zeros(n, dtype=bool)
        
        closes = df['Close'].values
        trends = df['Trend'].values
        strong_highs = df['Strong_High'].values
        strong_lows = df['Strong_Low'].values
        weak_highs = df['Weak_High'].values
        weak_lows = df['Weak_Low'].values
        
        for i in range(n):
            tr = trends[i]
            c = closes[i]
            sh = strong_highs[i]
            sl = strong_lows[i]
            wh = weak_highs[i]
            wl = weak_lows[i]
            
            if tr == 'bearish' and not np.isnan(sh) and not np.isnan(wl) and sh > wl:
                rng = sh - wl
                eq = wl + (rng * 0.50)
                f618 = wl + (rng * 0.618)
                f786 = wl + (rng * 0.786)
                
                equilibrium[i] = eq
                fib_618[i] = f618
                fib_786[i] = f786
                
                if c >= eq:
                    is_premium[i] = True
                if f618 <= c <= sh:
                    is_ote[i] = True
                    
            elif tr == 'bullish' and not np.isnan(sl) and not np.isnan(wh) and wh > sl:
                rng = wh - sl
                eq = sl + (rng * 0.50)
                f618 = wh - (rng * 0.618) # 61.8% pullback down into discount
                f786 = wh - (rng * 0.786) # 78.6% pullback down into discount
                
                equilibrium[i] = eq
                fib_618[i] = f618
                fib_786[i] = f786
                
                if c <= eq:
                    is_discount[i] = True
                if sl <= c <= f618:
                    is_ote[i] = True
                    
        df['Equilibrium'] = equilibrium
        df['Fib_618'] = fib_618
        df['Fib_786'] = fib_786
        df['In_Premium'] = is_premium
        df['In_Discount'] = is_discount
        df['In_OTE'] = is_ote
        
        return df

    def is_entry_allowed(self, trade_type: str, price: float, strong_high: float, strong_low: float, weak_high: float, weak_low: float) -> bool:
        """
        Gating check for a specific price point:
        - trade_type == 'short' -> price must be in PREMIUM (above 50% Equilibrium)
        - trade_type == 'long'  -> price must be in DISCOUNT (below 50% Equilibrium)
        """
        if trade_type.lower() == 'short':
            ref_high = strong_high
            ref_low = weak_low if not np.isnan(weak_low) else strong_low
            if np.isnan(ref_high) or np.isnan(ref_low) or ref_high <= ref_low:
                return True # Fallback if range not fully defined
            eq = ref_low + (ref_high - ref_low) * 0.50
            return price >= eq
            
        elif trade_type.lower() == 'long':
            ref_low = strong_low
            ref_high = weak_high if not np.isnan(weak_high) else strong_high
            if np.isnan(ref_high) or np.isnan(ref_low) or ref_high <= ref_low:
                return True
            eq = ref_low + (ref_high - ref_low) * 0.50
            return price <= eq
            
        return False

