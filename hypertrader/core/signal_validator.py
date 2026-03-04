"""
core/signal_validator.py — Validação de sinais antes de executar trades.

Princípio Clean Code: "Guard clauses reduzem aninhamento e tornam o código legível."
"""
import logging
from typing import Tuple

logger = logging.getLogger("SignalValidator")


class SignalValidator:
    """
    Valida preços e direção de um sinal antes de qualquer execução.
    Garante que SL/TP fazem sentido geometricamente.
    """

    def validate(self, direction: str, entry: float,
                 sl: float, tp1: float, tp2: float) -> Tuple[bool, str]:
        """
        Retorna (True, "OK") se válido,
        ou (False, "motivo") se inválido.
        """
        if entry <= 0 or sl <= 0 or tp1 <= 0:
            return False, f"Preços inválidos: entry={entry} sl={sl} tp1={tp1}"

        if direction == "LONG":
            return self._validate_long(entry, sl, tp1, tp2)

        if direction == "SHORT":
            return self._validate_short(entry, sl, tp1, tp2)

        return False, f"Direção desconhecida: {direction}"

    def _validate_long(self, entry, sl, tp1, tp2) -> Tuple[bool, str]:
        if sl >= entry:
            return False, f"LONG: SL {sl:.4f} deve ser ABAIXO da entrada {entry:.4f}"
        if tp1 <= entry:
            return False, f"LONG: TP1 {tp1:.4f} deve ser ACIMA da entrada {entry:.4f}"
        if tp2 > 0 and tp2 <= tp1:
            return False, f"LONG: TP2 {tp2:.4f} deve ser ACIMA do TP1 {tp1:.4f}"
        return True, "OK"

    def _validate_short(self, entry, sl, tp1, tp2) -> Tuple[bool, str]:
        if sl <= entry:
            return False, f"SHORT: SL {sl:.4f} deve ser ACIMA da entrada {entry:.4f}"
        if tp1 >= entry:
            return False, f"SHORT: TP1 {tp1:.4f} deve ser ABAIXO da entrada {entry:.4f}"
        if tp2 > 0 and tp2 >= tp1:
            return False, f"SHORT: TP2 {tp2:.4f} deve ser ABAIXO do TP1 {tp1:.4f}"
        return True, "OK"
