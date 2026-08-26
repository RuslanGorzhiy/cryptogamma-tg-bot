"""
Хранение последнего снимка метрик между запусками — нужно, чтобы
считать динамику (дельту Net GEX, C/P ratio, разворот dealer bias),
а не оценивать сигнал по одной изолированной точке.

Формат хранения — простой JSON-файл вида:
    {
      "BTC": {"net_gamma": ..., "put_call_ratio": ..., "dealer_bias": ..., ...},
      "ETH": {...}
    }

Важный нюанс для GitHub Actions: каждый запуск workflow выполняется на
чистой виртуальной машине, поэтому файл сам по себе НЕ сохраняется
между запусками — его нужно закоммитить обратно в репозиторий (это
сделано в .github/workflows/alert.yml последним шагом). При локальном
запуске bot.py файл просто лежит на диске между вызовами команд.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("state/last_snapshot.json")


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict:
    """Читает весь файл состояния. Отсутствие файла — не ошибка (первый запуск)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Не удалось прочитать файл состояния %s: %s", path, exc)
        return {}


def load_previous(asset: str, path: Path = DEFAULT_STATE_PATH) -> Optional[dict]:
    """Возвращает сохранённый снимок для конкретного актива, если он есть."""
    return load_state(path).get(asset.upper())


def save_previous(asset: str, data: dict, path: Path = DEFAULT_STATE_PATH) -> None:
    """Обновляет запись по одному активу, не трогая остальные, и сохраняет файл."""
    state = load_state(path)
    state[asset.upper()] = data
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Не удалось записать файл состояния %s: %s", path, exc)
