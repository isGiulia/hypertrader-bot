"""core/journal.py — Journal (log) persistente de eventos.

Objetivo:
- Guardar histórico "para sempre" sem depender do state.json.
- Registrar eventos importantes de forma append-only (NDJSON).

Eventos típicos:
- bot_start, sync, trade_open, trade_close, risk_pause, error
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List


class TradeJournal:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    def log(self, event_type: str, payload: Dict[str, Any] | None = None):
        rec: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
        }
        if payload:
            rec.update(payload)

        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def tail(self, n: int = 20, event_type: str | None = None) -> List[Dict[str, Any]]:
        """Retorna os últimos N eventos (filtrando por tipo, se passado)."""
        if n <= 0:
            return []
        if not os.path.exists(self.path):
            return []

        # Leitura simples (arquivo tende a ser pequeno no começo). Se ficar grande,
        # ainda assim NDJSON é fácil de analisar externamente.
        buf: deque = deque(maxlen=n)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if event_type and obj.get("type") != event_type:
                        continue
                    buf.append(obj)
        except Exception:
            return []
        return list(buf)
