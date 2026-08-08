import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_SERVICE_TOKEN", "test-service-token")

from telegram_bot.bot import backend_call, load_offset, save_offset


class TelegramOffsetTests(unittest.TestCase):
    def test_missing_or_invalid_offset_defaults_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "offset"
            self.assertEqual(load_offset(path), 0)
            path.write_text("not-an-offset", encoding="utf-8")
            self.assertEqual(load_offset(path), 0)

    def test_offset_round_trips_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "offset"
            save_offset(42, path)

            self.assertEqual(load_offset(path), 42)
            self.assertFalse(path.with_name(".offset.tmp").exists())

    def test_backend_call_accepts_empty_data_response(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"success": true, "data": null}'

        with patch("telegram_bot.bot.urllib.request.urlopen", return_value=Response()):
            self.assertIsNone(backend_call("/api/internal/telegram/claim", {}))


if __name__ == "__main__":
    unittest.main()
