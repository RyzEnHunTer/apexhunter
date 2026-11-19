import unittest
from datetime import datetime, timezone, timedelta
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.news_guardian import NewsGuardian

class TestNewsGuardian(unittest.TestCase):
    def setUp(self):
        self.guardian = NewsGuardian(
            refresh_interval_hours=12,
            auto_fetch=False # Disable web requests in unit tests
        )
        
        # Inject mock high-impact events
        # Event 1: US Non-Farm Payrolls (NFP) at 12:30 UTC
        self.nfp_time = datetime(2026, 2, 6, 12, 30, tzinfo=timezone.utc)
        self.guardian.high_impact_events = [
            {
                'title': 'Non-Farm Employment Change',
                'currency': 'USD',
                'time': self.nfp_time
            },
            {
                'title': 'ECB Monetary Policy Statement',
                'currency': 'EUR',
                'time': datetime(2026, 2, 6, 13, 15, tzinfo=timezone.utc)
            }
        ]

    def test_currency_mapping(self):
        self.assertEqual(self.guardian.get_driving_currencies('EURUSD'), ['EUR', 'USD'])
        self.assertEqual(self.guardian.get_driving_currencies('GBPUSD'), ['GBP', 'USD'])
        self.assertEqual(self.guardian.get_driving_currencies('USDJPY'), ['USD', 'JPY'])
        self.assertEqual(self.guardian.get_driving_currencies('XAUUSD'), ['USD'])
        self.assertEqual(self.guardian.get_driving_currencies('NAS100'), ['USD'])
        self.assertEqual(self.guardian.get_driving_currencies('USTEC'), ['USD'])

    def test_auto_close_trigger_10m_before(self):
        # Current time: 12:22 UTC (8 minutes before NFP at 12:30 UTC)
        t_now = self.nfp_time - timedelta(minutes=8)
        
        # EURUSD affected by USD -> should close!
        close_eur, desc_eur = self.guardian.should_close_before_news('EURUSD', t_now, close_lead_min=10)
        self.assertTrue(close_eur)
        self.assertIn("Non-Farm", desc_eur)

        # NAS100 affected by USD -> should close!
        close_nas, desc_nas = self.guardian.should_close_before_news('NAS100', t_now, close_lead_min=10)
        self.assertTrue(close_nas)
        self.assertIn("Non-Farm", desc_nas)

        # XAUUSD affected by USD -> should close!
        close_gold, _ = self.guardian.should_close_before_news('XAUUSD', t_now, close_lead_min=10)
        self.assertTrue(close_gold)

        # AUDCAD has no USD or EUR exposure -> should NOT close!
        close_cad, _ = self.guardian.should_close_before_news('AUDCAD', t_now, close_lead_min=10)
        self.assertFalse(close_cad)

    def test_no_close_when_news_far_away(self):
        # Current time: 11:30 UTC (60 minutes before NFP)
        t_now = self.nfp_time - timedelta(minutes=60)
        close_eur, _ = self.guardian.should_close_before_news('EURUSD', t_now, close_lead_min=10)
        self.assertFalse(close_eur)

    def test_entry_embargo_20m_before_and_15m_after(self):
        # 18 minutes before NFP -> Within 20m window -> Embargo active!
        t_before = self.nfp_time - timedelta(minutes=18)
        emb_before, _ = self.guardian.is_news_embargo('EURUSD', t_before, embargo_before_min=20, embargo_after_min=15)
        self.assertTrue(emb_before)

        # 10 minutes after NFP -> Within 15m post-window -> Embargo active!
        t_after = self.nfp_time + timedelta(minutes=10)
        emb_after, _ = self.guardian.is_news_embargo('EURUSD', t_after, embargo_before_min=20, embargo_after_min=15)
        self.assertTrue(emb_after)

        # 25 minutes after NFP (12:55 UTC) -> AUDUSD (USD only) is completely clear of NFP embargo!
        t_safe = self.nfp_time + timedelta(minutes=25)
        emb_safe, _ = self.guardian.is_news_embargo('AUDUSD', t_safe, embargo_before_min=20, embargo_after_min=15)
        self.assertFalse(emb_safe)

    def test_rate_limit_backoff_prevents_api_spam(self):
        """Verifies that an HTTP 429 error triggers a backoff cooldown instead of retrying every cycle."""
        from unittest.mock import patch
        import urllib.error

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url='https://nfs.faireconomy.media/ff_calendar_thisweek.json',
                code=429,
                msg='Too Many Requests',
                hdrs={},
                fp=None
            )

            # First call fails with 429
            self.guardian.refresh_calendar(force=True)
            self.assertEqual(mock_urlopen.call_count, 1)

            # Second call (e.g. 1 second later in main loop) MUST NOT call urlopen again
            self.guardian.refresh_calendar(force=False)
            self.assertEqual(mock_urlopen.call_count, 1, "Failed request must not trigger immediate repeated API call!")

if __name__ == '__main__':
    unittest.main()
