import unittest
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from live.notifier import TradeNotifier, NotificationManager, format_dual_time


class TestNotifier(unittest.TestCase):
    def test_dynamic_config_update(self):
        notifier = TradeNotifier({
            "active_platform": "telegram",
            "telegram_bot_token": "TEST_TOKEN_123",
            "telegram_chat_id": "-100123456",
            "discord_webhook_url": "https://discord.com/webhook/test"
        })
        self.assertEqual(notifier.active_platform, "telegram")
        self.assertEqual(notifier.tg_token, "TEST_TOKEN_123")
        self.assertEqual(notifier.tg_chat_id, "-100123456")
        self.assertEqual(notifier.discord_url, "https://discord.com/webhook/test")

        # Update to Discord only
        notifier.update_config({"active_platform": "discord"})
        self.assertEqual(notifier.active_platform, "discord")

        # Update to None (Mute)
        notifier.update_config({"active_platform": "none"})
        self.assertEqual(notifier.active_platform, "none")

    def test_test_connection_validation(self):
        # Empty tokens should return clean error tuples without throwing exceptions
        ok, msg = TradeNotifier.test_connection("telegram", "", "")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

        ok, msg = TradeNotifier.test_connection("discord", "")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

        ok, msg = TradeNotifier.test_connection("unknown_platform", "something")
        self.assertFalse(ok)
        self.assertIn("unknown", msg.lower())

    def test_compatibility_alias(self):
        self.assertIs(NotificationManager, TradeNotifier)

    def test_dual_time_formatter(self):
        formatted = format_dual_time()
        self.assertIn("UTC", formatted)
        self.assertIn("IST", formatted)


if __name__ == "__main__":
    unittest.main()
