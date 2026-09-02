from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from bot_service import TelegramBotService


percent, speed, eta = TelegramBotService._progress_metrics(5 * 1024 * 1024, 10 * 1024 * 1024, 5.0)
assert round(percent, 1) == 50.0
assert round(speed, 2) == 1.0
assert round(eta, 1) == 5.0

percent, speed, eta = TelegramBotService._progress_metrics(0, 0, 1.0)
assert percent == 0.0 and speed == 0.0 and eta is None

percent, speed, eta = TelegramBotService._progress_metrics(20, 10, 1.0)
assert percent == 100.0
print('PROGRESS_METRICS_OK')
