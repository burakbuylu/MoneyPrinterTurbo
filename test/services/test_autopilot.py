import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.services import autopilot, bgm


class TestAutopilotParsing(unittest.TestCase):
    def test_parse_line_plain(self):
        self.assertEqual(autopilot.parse_line("My title"), ("My title", None))

    def test_parse_line_with_aspect_override(self):
        self.assertEqual(autopilot.parse_line("My title | 16:9"), ("My title", "16:9"))
        self.assertEqual(autopilot.parse_line("Topic | shorts"), ("Topic", "9:16"))
        self.assertEqual(autopilot.parse_line("Topic | yatay"), ("Topic", "16:9"))

    def test_parse_line_skips_comments_and_blanks(self):
        self.assertIsNone(autopilot.parse_line(""))
        self.assertIsNone(autopilot.parse_line("   "))
        self.assertIsNone(autopilot.parse_line("# a comment"))

    def test_parse_line_unknown_aspect_falls_back_to_none(self):
        self.assertEqual(autopilot.parse_line("Title | banana"), ("Title", None))

    def test_normalize_aspect_aliases(self):
        self.assertEqual(autopilot.normalize_aspect("portrait"), "9:16")
        self.assertEqual(autopilot.normalize_aspect("LANDSCAPE"), "16:9")
        self.assertEqual(autopilot.normalize_aspect("square"), "1:1")
        self.assertIsNone(autopilot.normalize_aspect("nope"))


class TestAutopilotQueue(unittest.TestCase):
    def test_next_pending_skips_processed(self):
        titles = [("A", None), ("B", "16:9"), ("C", None)]
        state = {"processed": [autopilot._norm_title("A")]}
        self.assertEqual(autopilot.next_pending(titles, state), ("B", "16:9"))

    def test_next_pending_none_when_all_processed(self):
        titles = [("A", None)]
        state = {"processed": [autopilot._norm_title("A")]}
        self.assertIsNone(autopilot.next_pending(titles, state))

    def test_record_failure_gives_up_after_threshold(self):
        state = autopilot._empty_state()
        norm = autopilot._norm_title("X")
        for _ in range(autopilot.MAX_FAILURES):
            autopilot._record_failure(state, norm)
        self.assertIn(norm, state["processed"])


class TestBgmFallback(unittest.TestCase):
    def setUp(self):
        self.original_app_config = dict(config.app)

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)

    def test_ensure_bgm_local_returns_none(self):
        config.app["bgm_source"] = "local"
        self.assertEqual(bgm.ensure_bgm(), (None, None))

    def test_fetch_bgm_without_client_id_returns_none(self):
        config.app["jamendo_client_id"] = ""
        self.assertEqual(bgm.fetch_bgm(), (None, None))

    def test_license_label(self):
        self.assertEqual(
            bgm._license_label("https://creativecommons.org/licenses/by-sa/3.0/"),
            "CC BY-SA 3.0",
        )


if __name__ == "__main__":
    unittest.main()
