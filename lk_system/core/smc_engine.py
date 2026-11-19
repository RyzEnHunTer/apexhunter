import pandas as pd
import numpy as np
from .market_structure import MarketStructure
from .session_manager import SessionManager
from .imbalance import ImbalanceDetector
from .dealing_range import DealingRangeEngine

class SMCEngine:
    """
    The Master SMC Orchestrator implementing Lewis Kelly's complete system:
    1. Multi-Timeframe Bias: Analyzes M15 Trend (Bullish/Bearish) and Structure.
    2. Wholesale/Retail Pricing: Filters trades strictly by 50% Equilibrium (Discount for Longs, Premium for Shorts).
    3. Liquidity Engineering: Detects sweeps of Session Extremes (Asia High/Low, London High/Low, PDH/PDL).
    4. Institutional POIs: Selects valid Order Blocks with FVG displacement.
    5. Invalidation & Risk Setup: Computes tight logical stop losses at sweep extremes and targeted weak swing TP.
    """
    def __init__(self, pullback_candles=3):
        self.market_structure = MarketStructure(pullback_candles=pullback_candles)
        self.session_manager = SessionManager()
        self.imbalance_detector = ImbalanceDetector()
        self.dealing_range = DealingRangeEngine()
        
    def generate_signals(self, df_m15: pd.DataFrame) -> pd.DataFrame:
        """
        Processes M15 dataframe to generate Lewis Kelly high-probability trade setups.
        """
        # 1. Update Core Subsystems
        df = self.session_manager.update(df_m15)
        df = self.market_structure.update(df)
        df = self.imbalance_detector.detect(df)
        df = self.dealing_range.calculate_range(df)
        
        n = len(df)
        signals = np.zeros(n, dtype=int)
        setups = np.full(n, 'None', dtype=object)
        entry_prices = np.full(n, np.nan)
        stop_losses = np.full(n, np.nan)
        take_profits = np.full(n, np.nan)
        rr_ratios = np.full(n, np.nan)
        
        # State machine tracking for mitigation / confirmation
        pending_setup = None
        pending_entry = 0.0
        pending_sl = 0.0
        pending_tp = 0.0
        pending_bar_idx = -1
        
        closes = df['Close'].values
        highs = df['High'].values
        lows = df['Low'].values
        trends = df['Trend'].values
        sessions = df['Active_Session'].values
        
        in_prem = df['In_Premium'].values
        in_disc = df['In_Discount'].values
        
        asia_sw_h = df['Asia_Swept_High'].values
        asia_sw_l = df['Asia_Swept_Low'].values
        lon_sw_h = df['London_Swept_High'].values
        lon_sw_l = df['London_Swept_Low'].values
        
        bear_fvg = df['Bearish_FVG'].values
        bull_fvg = df['Bullish_FVG'].values
        bear_ob_l = df['Bearish_OB_Low'].values
        bull_ob_h = df['Bullish_OB_High'].values
        
        strong_h = df['Strong_High'].values
        strong_l = df['Strong_Low'].values
        weak_h = df['Weak_High'].values
        weak_l = df['Weak_Low'].values
        
        for i in range(1, n):
            c = closes[i]
            h = highs[i]
            l = lows[i]
            sess = sessions[i]
            
            # --- Check Active Pending Setup Mitigation ---
            if pending_setup == 'Short':
                # Pullback into Bearish POI
                if h >= pending_entry:
                    signals[i] = -1
                    setups[i] = 'Mitigated_Short'
                    entry_prices[i] = pending_entry
                    stop_losses[i] = pending_sl
                    take_profits[i] = pending_tp
                    risk = abs(pending_entry - pending_sl)
                    reward = abs(pending_entry - pending_tp)
                    rr_ratios[i] = reward / risk if risk > 0 else 0
                    pending_setup = None
                    continue
                elif i - pending_bar_idx > 8 or sess not in ['London', 'NY']:
                    # Setup expired after 2 hours (8 x 15m bars) or session closed
                    pending_setup = None
                    
            elif pending_setup == 'Long':
                # Pullback into Bullish POI
                if l <= pending_entry:
                    signals[i] = 1
                    setups[i] = 'Mitigated_Long'
                    entry_prices[i] = pending_entry
                    stop_losses[i] = pending_sl
                    take_profits[i] = pending_tp
                    risk = abs(pending_entry - pending_sl)
                    reward = abs(pending_entry - pending_tp)
                    rr_ratios[i] = reward / risk if risk > 0 else 0
                    pending_setup = None
                    continue
                elif i - pending_bar_idx > 8 or sess not in ['London', 'NY']:
                    pending_setup = None
                    
            # --- Look for New Lewis Kelly Setups ---
            if sess in ['London', 'NY'] and pending_setup is None:
                tr = trends[i]
                
                # Check for active session liquidity sweeps
                swept_high = False
                swept_low = False
                
                if sess == 'London':
                    swept_high = asia_sw_h[i]
                    swept_low = asia_sw_l[i]
                elif sess == 'NY':
                    swept_high = lon_sw_h[i] or asia_sw_h[i]
                    swept_low = lon_sw_l[i] or asia_sw_l[i]
                    
                # ─── SHORT SETUP (Sell Model) ───
                # 1. Trend must be Bearish
                # 2. Price MUST be in PREMIUM (> 50% Equilibrium)
                # 3. Liquidity Raid: Swept high of previous session or swing high
                # 4. Bearish FVG displacement formed
                if tr == 'bearish' and in_prem[i] and (swept_high or df['Sweep_High'].iloc[i]):
                    if bear_fvg[i]:
                        # Entry at Bearish OB / FVG threshold
                        entry_lvl = bear_ob_l[i] if not np.isnan(bear_ob_l[i]) else df['FVG_Bottom'].iloc[i]
                        # Stop loss placed tightly above the sweep candle high (+ 1 pip buffer)
                        sl_lvl = h + 0.0001
                        # Target: Targeted Weak Low
                        tp_lvl = weak_l[i] if not np.isnan(weak_l[i]) else (l - 0.0030)
                        
                        risk = abs(entry_lvl - sl_lvl)
                        reward = abs(entry_lvl - tp_lvl)
                        
                        # Minimum 1:2.5 R:R filter
                        if risk > 0 and (reward / risk) >= 2.5:
                            pending_setup = 'Short'
                            pending_entry = entry_lvl
                            pending_sl = sl_lvl
                            pending_tp = tp_lvl
                            pending_bar_idx = i
                            
                # ─── LONG SETUP (Buy Model) ───
                # 1. Trend must be Bullish
                # 2. Price MUST be in DISCOUNT (< 50% Equilibrium)
                # 3. Liquidity Raid: Swept low of previous session or swing low
                # 4. Bullish FVG displacement formed
                elif tr == 'bullish' and in_disc[i] and (swept_low or df['Sweep_Low'].iloc[i]):
                    if bull_fvg[i]:
                        entry_lvl = bull_ob_h[i] if not np.isnan(bull_ob_h[i]) else df['FVG_Top'].iloc[i]
                        sl_lvl = l - 0.0001
                        tp_lvl = weak_h[i] if not np.isnan(weak_h[i]) else (h + 0.0030)
                        
                        risk = abs(entry_lvl - sl_lvl)
                        reward = abs(entry_lvl - tp_lvl)
                        
                        if risk > 0 and (reward / risk) >= 2.5:
                            pending_setup = 'Long'
                            pending_entry = entry_lvl
                            pending_sl = sl_lvl
                            pending_tp = tp_lvl
                            pending_bar_idx = i

        df['Signal'] = signals
        df['Setup'] = setups
        df['Entry_Price'] = entry_prices
        df['Stop_Loss'] = stop_losses
        df['Take_Profit'] = take_profits
        df['Risk_Reward'] = rr_ratios
        
        return df
