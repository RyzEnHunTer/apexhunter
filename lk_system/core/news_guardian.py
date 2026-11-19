"""
News Guardian Module - Production Grade
=======================================
Protects prop firm accounts from high-impact news slippage & rule breaches:
1. 10-Minute Pre-News Auto-Close:
   - Closes open positions on affected instruments 10m before event at tight baseline spreads.
2. 20m Pre-News & 15m Post-News Entry Embargo:
   - Blocks opening new trades into pre-news chop and post-news spread spikes.
3. 12-Hour Recurring Forex Factory Auto-Refresh:
   - Automatically syncs the latest high-impact calendar every 12 hours with local disk caching.
4. Universal Multi-Asset Mapping:
   - Explicitly maps Gold (XAUUSD) and Nasdaq (NAS100, USTEC) to USD high-impact releases.
"""

import os
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Tuple, List, Dict, Any, Optional
import pandas as pd
import pytz

class NewsGuardian:
    SYMBOL_MAP: Dict[str, List[str]] = {
        'XAUUSD': ['USD'],
        'GOLD': ['USD'],
        'NAS100': ['USD'],
        'USTEC': ['USD'],
        'US30': ['USD'],
        'SPX500': ['USD'],
        'GER30': ['EUR'],
        'GER40': ['EUR'],
        'UK100': ['GBP'],
    }

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        refresh_interval_hours: int = 12,
        auto_fetch: bool = True
    ):
        if cache_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_dir = os.path.join(base_dir, "live", "logs")
            
        self.cache_dir = cache_dir
        self.refresh_interval_hours = refresh_interval_hours
        self.retry_cooldown_hours = 1.0 # Wait at least 1 hour before retrying on API error/rate-limit
        self.utc = pytz.timezone("UTC")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.high_impact_events: List[Dict[str, Any]] = []
        self.last_refresh_time: Optional[datetime] = None

        if auto_fetch:
            self.refresh_calendar(force=False)

    def clean_symbol(self, symbol: str) -> str:
        """Strips broker suffixes."""
        return symbol.replace('.r', '').replace('.m', '').replace('/', '').upper()

    def get_driving_currencies(self, symbol: str) -> List[str]:
        """Returns the currencies that trigger news protection for this instrument."""
        clean_sym = self.clean_symbol(symbol)
        if clean_sym in self.SYMBOL_MAP:
            return self.SYMBOL_MAP[clean_sym]
        if len(clean_sym) == 6:
            return [clean_sym[:3], clean_sym[3:]]
        return ['USD']

    def refresh_calendar(self, force: bool = False):
        """Fetches fresh calendar from Forex Factory if refresh interval elapsed."""
        now_utc = datetime.now(self.utc)
        cache_file = os.path.join(self.cache_dir, "news_calendar_latest.json")

        # 1. In-memory cooldown check
        if not force and self.last_refresh_time:
            elapsed = (now_utc - self.last_refresh_time).total_seconds() / 3600.0
            if elapsed < self.refresh_interval_hours:
                return

        # 2. Disk cache freshness check (avoid unnecessary web requests on startup)
        if not force and os.path.exists(cache_file):
            try:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(cache_file), tz=self.utc)
                cache_age = (now_utc - file_mtime).total_seconds() / 3600.0
                if cache_age < self.refresh_interval_hours:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cached_data = json.load(f)
                    if cached_data and isinstance(cached_data, list):
                        self._parse_events(cached_data)
                        self.last_refresh_time = file_mtime
                        return
            except Exception:
                pass

        data = None
        try:
            req = urllib.request.Request(
                'https://nfs.faireconomy.media/ff_calendar_thisweek.json',
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) InstitutionalDCCBot/2.0'}
            )
            ctx = None
            try:
                import ssl
                ctx = ssl.create_default_context()
            except Exception:
                pass

            try:
                response = urllib.request.urlopen(req, timeout=10, context=ctx)
            except Exception as e:
                err_str = str(e).lower()
                if "certificate" in err_str or "ssl" in err_str or "cert" in err_str:
                    import ssl
                    response = urllib.request.urlopen(req, timeout=10, context=ssl._create_unverified_context())
                else:
                    raise e

            data = json.loads(response.read().decode('utf-8'))
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.last_refresh_time = now_utc
            print(f"[NewsGuardian] Successfully refreshed Forex Factory calendar ({len(data)} events).")
        except Exception as e:
            # Prevent rapid retry loops: enforce cooldown on failures (e.g. 429 Too Many Requests)
            backoff_delta = timedelta(hours=max(0.1, self.refresh_interval_hours - self.retry_cooldown_hours))
            self.last_refresh_time = now_utc - backoff_delta
            print(f"[NewsGuardian] Warning: Failed to fetch live news ({e}). Falling back to local cache (cooldown: {self.retry_cooldown_hours:.0f}h).")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    data = []
            else:
                data = []

        self._parse_events(data)

    def _parse_events(self, data: Optional[List[Dict[str, Any]]]):
        self.high_impact_events = []
        if not data:
            return

        for item in data:
            if item.get("impact") == "High":
                try:
                    event_dt = pd.to_datetime(item["date"]).astimezone(self.utc)
                    self.high_impact_events.append({
                        "title": item.get("title", "High-Impact News"),
                        "currency": item.get("country", "").upper(),
                        "time": event_dt,
                        "forecast": item.get("forecast", "-"),
                        "previous": item.get("previous", "-"),
                        "impact": item.get("impact", "High")
                    })
                except Exception:
                    pass

    def get_upcoming_events(self, now_utc: Optional[datetime] = None, hours_ahead: int = 48) -> List[Dict[str, Any]]:
        """Returns sorted list of upcoming high-impact events within the lookahead window."""
        if now_utc is None:
            now_utc = datetime.now(self.utc)
        elif now_utc.tzinfo is None:
            now_utc = self.utc.localize(now_utc)
        else:
            now_utc = now_utc.astimezone(self.utc)

        cutoff = now_utc + timedelta(hours=hours_ahead)
        upcoming = [
            e for e in self.high_impact_events
            if (now_utc - timedelta(minutes=30)) <= e["time"] <= cutoff
        ]
        upcoming.sort(key=lambda x: x["time"])
        return upcoming

    def should_close_before_news(
        self,
        symbol: str,
        current_dt: datetime,
        close_lead_min: int = 10
    ) -> Tuple[bool, str]:
        """
        Returns (True, description) if high-impact news on an affected currency 
        is occurring within the next close_lead_min (e.g. 10m before).
        """
        if current_dt.tzinfo is None:
            current_dt = self.utc.localize(current_dt)
        else:
            current_dt = current_dt.astimezone(self.utc)

        currencies = self.get_driving_currencies(symbol)

        for event in self.high_impact_events:
            if event["currency"] in currencies:
                diff_sec = (event["time"] - current_dt).total_seconds()
                # Event is in future within close_lead_min window
                if 0 <= diff_sec <= (close_lead_min * 60):
                    mins_left = max(0, int(diff_sec / 60))
                    desc = f"{event['currency']} {event['title']} in {mins_left}m (at {event['time'].strftime('%H:%M')} UTC)"
                    return True, desc
        return False, ""

    def is_news_embargo(
        self,
        symbol: str,
        current_dt: datetime,
        embargo_before_min: int = 20,
        embargo_after_min: int = 15
    ) -> Tuple[bool, str]:
        """
        Returns (True, reason) if current time is within embargo window 
        (20m before to 15m after). Blocks new trade entries.
        """
        if current_dt.tzinfo is None:
            current_dt = self.utc.localize(current_dt)
        else:
            current_dt = current_dt.astimezone(self.utc)

        currencies = self.get_driving_currencies(symbol)

        for event in self.high_impact_events:
            if event["currency"] in currencies:
                diff_sec = (current_dt - event["time"]).total_seconds()
                # Negative diff_sec = before event; positive = after event
                if -(embargo_before_min * 60) <= diff_sec <= (embargo_after_min * 60):
                    mins = int(abs(diff_sec) / 60)
                    timing = f"{mins}m before" if diff_sec < 0 else f"{mins}m after"
                    desc = f"{event['currency']} {event['title']} ({timing} release at {event['time'].strftime('%H:%M')} UTC)"
                    return True, desc
        return False, ""
