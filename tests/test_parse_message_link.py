from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from downloader import parse_message_link  # noqa: E402


class ParseMessageLinkTest(unittest.TestCase):
    def test_private_channel_link(self):
        self.assertEqual(
            parse_message_link("https://t.me/c/3777984784/45"),
            (-1003777984784, 45),
        )

    def test_public_channel_link(self):
        self.assertEqual(
            parse_message_link("https://t.me/channel_username/45"),
            ("channel_username", 45),
        )

    def test_invalid_link(self):
        self.assertEqual(parse_message_link("https://example.com/message/45"), (None, None))


if __name__ == "__main__":
    unittest.main()
