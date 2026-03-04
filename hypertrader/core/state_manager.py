"""
core/state_manager.py — Persistência do estado do bot em disco.

Princípio Clean Code: "Uma classe deve ter uma única responsabilidade (SRP)."
Salvar e carregar estado é uma responsabilidade própria — fica aqui.
O resto do sistema chama save() e load() sem saber como funciona.
"""
import os
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List

from hypertrader import config

logger = logging.getLogger("StateManager")


@dataclass
class TradingState:
    """Representa o estado completo que é persistido entre reinicializações."""
    active_positions:   Dict  = field(default_factory=dict)
    trade_history:      List  = field(default_factory=list)
    stats:              Dict  = field(default_factory=lambda: {
        "total": 0, "wins": 0, "losses": 0, "best": 0.0, "worst": 0.0
    })
    session_pnl:        float = 0.0
    daily_pnl:          float = 0.0
    daily_trades_count: int   = 0
    peak_equity:        float = 0.0
    dry_run_mode:       bool  = config.DRY_RUN


class StateManager:
    """
    Gerencia a persistência do estado do bot.
    Usa arquivo temporário + replace atômico para evitar corrupção.
    """

    def __init__(self, filepath: str = config.STATE_FILE):
        self._filepath = filepath

    def save(self, state: TradingState):
        """Salva o estado em disco de forma atômica (sem risco de corrupção)."""
        try:
            payload = {
                "dry_run_mode":       state.dry_run_mode,
                "active_positions":   state.active_positions,
                "trade_history":      state.trade_history[-100:],   # mantém só os 100 últimos
                "stats":              state.stats,
                "session_pnl":        state.session_pnl,
                "daily_pnl":          state.daily_pnl,
                "daily_trades_count": state.daily_trades_count,
                "peak_equity":        state.peak_equity,
                "saved_at":           datetime.now(timezone.utc).isoformat(),
            }
            tmp_path = self._filepath + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(payload, f, indent=2, default=str)
            os.replace(tmp_path, self._filepath)
        except Exception as e:
            logger.error(f"save_state: {e}")

    def load(self) -> TradingState:
        """
        Carrega estado do disco.
        Se o modo (dry_run vs live) mudou, zera as posições para evitar
        posições fantasmas de simulação aparecerem no modo real.
        """
        state = TradingState()

        if not os.path.exists(self._filepath):
            return state

        try:
            with open(self._filepath) as f:
                saved = json.load(f)

            saved_dry = saved.get("dry_run_mode", None)
            mode_changed = saved_dry is not None and saved_dry != config.DRY_RUN

            if mode_changed:
                logger.warning(
                    f"Modo alterado (dry_run={saved_dry} → {config.DRY_RUN}) "
                    f"— posições anteriores ignoradas"
                )
                # Preserva histórico e stats mas descarta posições abertas
                state.trade_history      = saved.get("trade_history", [])
                state.stats              = saved.get("stats", state.stats)
                state.peak_equity        = saved.get("peak_equity", 0.0)
                return state

            state.active_positions   = saved.get("active_positions", {})
            state.trade_history      = saved.get("trade_history", [])
            state.stats              = saved.get("stats", state.stats)
            state.session_pnl        = saved.get("session_pnl", 0.0)
            state.daily_pnl          = saved.get("daily_pnl", 0.0)
            state.daily_trades_count = saved.get("daily_trades_count", 0)
            state.peak_equity        = saved.get("peak_equity", 0.0)
            state.dry_run_mode       = config.DRY_RUN

            logger.info(
                f"State carregado: {len(state.active_positions)} posições, "
                f"peak=${state.peak_equity:.2f}"
            )
        except Exception as e:
            logger.error(f"load_state: {e}")

        return state
