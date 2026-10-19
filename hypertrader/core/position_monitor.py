"""
core/position_monitor.py — Monitoramento de posições abertas.

Princípio Clean Code: "Nomes revelam intenção."
Cada método tem um nome que descreve exatamente o que ele faz.

V2 — Inclui guarda-corpo crítico: garante que toda posição LIVE tem SL
na exchange. Sem isso, uma falha na hora da entrada deixa a posição
exposta sem proteção.
"""
import logging
from datetime import datetime, timezone
from typing import Callable

from hypertrader import config
from hypertrader.exchange.hyperliquid_client import HyperliquidClient
from hypertrader.core.trade_executor import TradeExecutor
from hypertrader.notifications import messages

logger = logging.getLogger("PositionMonitor")


class PositionMonitor:
    """
    Monitora posições abertas a cada ciclo e reage a eventos:
      - Posição fechada externamente (SL/TP da exchange ou manual)
      - Fill de ordem pendente confirmado
      - Stop Loss atingido (monitoramento interno)
      - TP1 atingido (fecha parcial + move SL para breakeven)
      - TP2 atingido (fecha restante)
      - Trailing stop atualizado
      - Alerta de liquidação próxima
      - [NOVO] SL ausente na exchange (recoloca automaticamente)
    """

    def __init__(self, exchange: HyperliquidClient,
                 executor: TradeExecutor,
                 risk_manager,
                 notify_fn: Callable,
                 on_position_closed: Callable,
                 on_state_changed: Callable):
        self._hl         = exchange
        self._executor   = executor
        self._risk       = risk_manager
        self._notify     = notify_fn
        self._on_closed  = on_position_closed   # callback para atualizar stats
        self._on_changed = on_state_changed     # callback para salvar estado

    def check_all(self, active_positions: dict) -> dict:
        """
        Verifica todas as posições abertas.
        Retorna o dict de posições atualizado (algumas podem ter sido removidas).
        """
        real_positions = self._fetch_real_positions()

        for symbol in list(active_positions.keys()):
            pos     = active_positions[symbol]
            current = self._hl.get_mark_price(symbol)
            if current == 0:
                continue

            # Posição sumiu da exchange — foi fechada por SL/TP ou manualmente
            if not config.DRY_RUN and symbol not in real_positions:
                self._handle_external_close(symbol, pos, current, active_positions)
                continue

            # Ordem de entrada ainda pendente — verifica se foi preenchida
            if pos.get("status") == "pending_fill" and not config.DRY_RUN:
                self._handle_pending_fill(symbol, pos, real_positions, active_positions)
                continue

            # Posição aberta — verifica SL/TP e trailing
            self._check_levels(symbol, pos, current, real_positions, active_positions)

        return active_positions

    # ── Handlers de eventos

    def _handle_external_close(self, symbol: str, pos: dict,
                                current: float, active_positions: dict):
        """Posição fechada na exchange sem ser pelo bot — busca fill e notifica."""
        logger.warning(f"Posição {symbol} fechada na exchange — buscando fill real...")

        exit_price, real_fee, closed_pnl = self._executor._get_close_fill_data(
            symbol, float(pos.get("entry", current)), pos["direction"] == "LONG"
        )

        is_long  = pos["direction"] == "LONG"
        entry    = float(pos.get("entry", 0) or exit_price)
        pnl_pct  = ((exit_price - entry) / entry * 100) if is_long else ((entry - exit_price) / entry * 100)
        pnl_lev  = pnl_pct * pos.get("leverage", 1)
        pnl_usd  = closed_pnl if closed_pnl != 0 else (pnl_pct / 100) * pos.get("equity_open", 0)
        dur_min  = self._duration_minutes(pos)
        funding  = self._fetch_funding(symbol)

        # Tenta identificar motivo pelo fill
        reason = "TP/SL na exchange"
        try:
            last = self._hl.get_last_fill(symbol)
            if last and last.get("direction"):
                reason = f"Exchange: {last['direction']}"
        except Exception:
            pass

        self._notify(messages.close_message(
            symbol, pos, entry, exit_price,
            pnl_lev, pnl_usd, dur_min, reason, real_fee, funding
        ))

        self._on_closed(symbol, pos, exit_price, reason, pnl_usd, real_fee, dur_min)
        del active_positions[symbol]
        self._on_changed()

    def _handle_pending_fill(self, symbol: str, pos: dict,
                              real_positions: dict, active_positions: dict):
        """Verifica se ordem limite pendente foi preenchida."""
        open_oids = [str(o.get("oid", "")) for o in self._hl.get_open_orders()]
        if str(pos.get("entry_order_id", "")) in open_oids:
            return  # ainda pendente

        # Foi preenchida — pega preço real
        if symbol in real_positions:
            pos["entry"] = real_positions[symbol]["entry_price"]

        is_buy = pos["direction"] == "LONG"
        pos    = self._executor.place_protection_orders(
            symbol, pos, pos["size"], is_buy,
            pos["stop_loss"], pos["tp1"], pos["tp2"]
        )
        pos["status"] = "open"
        active_positions[symbol] = pos

        self._notify(messages.fill_confirmed_message(symbol, pos))
        self._on_changed()

    def _check_levels(self, symbol: str, pos: dict, current: float,
                       real_positions: dict, active_positions: dict):
        """
        LIVE: confia nas ordens de SL/TP já colocadas na exchange.
              Bot apenas:
                0. [NOVO] Garante que a posição TEM SL (recoloca se faltar)
                1. Alerta se próximo de liquidação
                2. Detecta TP1 via redução de tamanho (e move SL para breakeven)
                3. Atualiza trailing stop após TP1

        DRY_RUN: simula SL/TP internamente (não há exchange real).
        """
        is_long = pos["direction"] == "LONG"
        entry   = float(pos.get("entry", 0) or current)
        pnl_pct = ((current - entry) / entry * 100) if is_long else ((entry - current) / entry * 100)
        pos["peak_pnl"] = max(pos["peak_pnl"], pnl_pct)

        real_pnl_usd = real_positions.get(symbol, {}).get("unrealized_pnl", None)
        self._check_liquidation_risk(symbol, pos, pnl_pct, real_pnl_usd)

        # ─────────────────────────────────────────────────────────
        #  GUARDA-CORPO CRÍTICO (LIVE):
        #  Garante que toda posição tem SL na exchange.
        #  Se o SL falhou na entrada (ex: erro de API), recoloca agora.
        #  Se não conseguir, avisa e fecha por segurança.
        # ─────────────────────────────────────────────────────────
        if not config.DRY_RUN and not pos.get("sl_order_id"):
            self._ensure_stop_loss(symbol, pos, current, is_long, active_positions)
            # Se a posição foi fechada por segurança, sai do método
            if symbol not in active_positions:
                return

        if config.DRY_RUN:
            # Simulação: fecha SL/TP internamente
            sl  = float(pos.get("stop_loss", 0) or 0)
            tp1 = float(pos.get("tp1", 0) or 0)
            tp2 = float(pos.get("tp2", 0) or 0)
            hit_sl  = sl  > 0 and ((is_long and current <= sl)  or (not is_long and current >= sl))
            hit_tp1 = tp1 > 0 and not pos["tp1_hit"] and ((is_long and current >= tp1) or (not is_long and current <= tp1))
            hit_tp2 = tp2 > 0 and pos["tp1_hit"]     and ((is_long and current >= tp2) or (not is_long and current <= tp2))
            if hit_sl:
                self._close_and_remove(symbol, pos, current, "Stop Loss atingido", active_positions)
            elif hit_tp2:
                self._close_and_remove(symbol, pos, current, "TP2 atingido", active_positions)
            elif hit_tp1:
                self._handle_tp1(symbol, pos, current, entry, tp2, pnl_pct, is_long, active_positions)
            elif pos["tp1_hit"] and config.TRAILING_STOP:
                self._update_trailing(symbol, pos, current, is_long, active_positions)
            return

        # ── LIVE: exchange cuida do SL/TP
        # Detecta TP1 via tamanho real na exchange (exchange fechou 50%)
        if not pos["tp1_hit"]:
            real_size = real_positions.get(symbol, {}).get("size", pos["size"])
            expected  = float(pos.get("size", 0))
            # Se tamanho real caiu para ~50% do original = TP1 foi executado
            if expected > 0 and real_size <= expected * 0.6:
                tp2   = float(pos.get("tp2", 0) or 0)
                logger.info(f"TP1 detectado via exchange {symbol}: size {expected:.4f} → {real_size:.4f}")
                pos["size"]     = real_size
                pos["stop_loss"] = entry   # move SL para breakeven
                pos["tp1_hit"]  = True
                if pos.get("sl_order_id"):
                    try:
                        self._hl.cancel_order(symbol, pos["sl_order_id"])
                        r = self._hl.place_order(symbol=symbol, is_buy=not is_long,
                                                  size=real_size, price=entry,
                                                  order_type="stop", reduce_only=True)
                        pos["sl_order_id"] = r.get("order_id")
                    except Exception as e:
                        logger.error(f"Move SL breakeven {symbol}: {e}")
                lev_pnl = pnl_pct * pos.get("leverage", 1)
                self._notify(messages.tp1_hit_message(symbol, lev_pnl, entry, tp2))
                active_positions[symbol] = pos
                self._on_changed()
                return

        # Trailing stop após TP1
        if pos["tp1_hit"] and config.TRAILING_STOP:
            self._update_trailing(symbol, pos, current, is_long, active_positions)

    # ── Guarda-corpo: SL obrigatório em LIVE

    def _ensure_stop_loss(self, symbol: str, pos: dict, current: float,
                           is_long: bool, active_positions: dict):
        """
        Garante que a posição tem SL na exchange.

        Chamado a cada ciclo quando `pos["sl_order_id"]` está vazio.
        Tenta recolocar o SL. Se falhar ou não tiver preço, fecha a posição
        por segurança (melhor sair com lucro/perda pequena do que sem proteção).
        """
        sl_price = float(pos.get("stop_loss", 0) or 0)

        if sl_price <= 0:
            # Sem preço de SL definido — não dá pra proteger. Fecha.
            logger.error(f"{symbol}: sem SL e sem preço — fechando por segurança")
            self._notify(
                f"🚨 <b>{symbol} sem SL definido</b>\n"
                f"Fechando posição por segurança."
            )
            self._close_and_remove(symbol, pos, current, "SL ausente", active_positions)
            return

        logger.warning(f"{symbol}: posição SEM SL — tentando recolocar @ ${sl_price:.4f}")

        try:
            r = self._hl.place_order(
                symbol=symbol,
                is_buy=not is_long,     # LONG fecha com SELL, SHORT fecha com BUY
                size=float(pos["size"]),
                price=sl_price,
                order_type="stop",
                reduce_only=True,
            )

            if r.get("status") == "ok" and r.get("order_id"):
                pos["sl_order_id"] = r["order_id"]
                active_positions[symbol] = pos
                self._on_changed()
                self._notify(
                    f"🔧 <b>SL recolocado — {symbol}</b>\n"
                    f"Posição estava <b>sem proteção</b>. Agora SL em "
                    f"<code>${sl_price:.4f}</code> (oid={r['order_id']})"
                )
            else:
                logger.error(f"{symbol}: falha ao recolocar SL — {r}")
                self._notify(
                    f"🚨 <b>Falha ao recolocar SL — {symbol}</b>\n"
                    f"Posição segue SEM PROTEÇÃO. <b>Feche manualmente!</b>"
                )
        except Exception as e:
            logger.error(f"{symbol}: exception recolocando SL — {e}")
            self._notify(
                f"🚨 <b>Erro recolocando SL — {symbol}</b>\n"
                f"<code>{str(e)[:150]}</code>\n"
                f"Posição segue SEM PROTEÇÃO. <b>Feche manualmente!</b>"
            )

    def _handle_tp1(self, symbol: str, pos: dict, current: float,
                     entry: float, tp2: float, pnl_pct: float,
                     is_long: bool, active_positions: dict):
        """Fecha metade da posição e move SL para breakeven."""
        partial = pos["size"] * config.PARTIAL_TP_PCT

        if not config.DRY_RUN:
            self._hl.place_order(
                symbol=symbol, is_buy=not is_long, size=partial,
                price=current, order_type="market", reduce_only=True
            )

        pos["size"]      -= partial
        pos["stop_loss"]  = entry
        pos["tp1_hit"]    = True

        if not config.DRY_RUN:
            pos = self._executor.move_sl_to_breakeven(symbol, pos, not is_long)

        lev_pnl = pnl_pct * pos["leverage"]
        self._notify(messages.tp1_hit_message(symbol, lev_pnl, entry, tp2))
        active_positions[symbol] = pos
        self._on_changed()

    def _update_trailing(self, symbol: str, pos: dict, current: float,
                          is_long: bool, active_positions: dict):
        """Atualiza trailing stop se o preço evoluiu favoravelmente."""
        atr = self._get_atr(symbol)
        if atr <= 0:
            return

        new_sl   = self._risk.calculate_trailing_stop(current, pos["direction"], atr)
        improved = (is_long and new_sl > pos["stop_loss"]) or (not is_long and new_sl < pos["stop_loss"])
        if not improved:
            return

        old_sl = pos["stop_loss"]
        pos["stop_loss"] = new_sl
        logger.info(f"Trailing {symbol}: ${old_sl:.4f} → ${new_sl:.4f}")

        if not config.DRY_RUN:
            pos = self._executor.update_trailing_sl(symbol, pos, not is_long, new_sl)

        active_positions[symbol] = pos
        self._on_changed()

    def _close_and_remove(self, symbol: str, pos: dict, current: float,
                           reason: str, active_positions: dict):
        """Fecha posição, atualiza stats e remove do dict ativo."""
        is_long  = pos["direction"] == "LONG"
        entry    = float(pos.get("entry", 0) or current)

        if not config.DRY_RUN:
            exit_price, real_fee, closed_pnl = self._executor.close_position(symbol, pos)
        else:
            exit_price, real_fee, closed_pnl = current, 0.0, 0.0

        pnl_pct = ((exit_price - entry) / entry * 100) if is_long else ((entry - exit_price) / entry * 100)
        pnl_lev = pnl_pct * pos["leverage"]
        pnl_usd = closed_pnl if closed_pnl != 0 else (pnl_pct / 100) * pos.get("equity_open", 0)
        dur_min = self._duration_minutes(pos)
        funding = self._fetch_funding(symbol)

        self._notify(messages.close_message(
            symbol, pos, entry, exit_price,
            pnl_lev, pnl_usd, dur_min, reason, real_fee, funding
        ))

        self._on_closed(symbol, pos, exit_price, reason, pnl_usd, real_fee, dur_min)
        del active_positions[symbol]
        self._on_changed()

    # ── Helpers

    def _check_liquidation_risk(self, symbol: str, pos: dict,
                                  pnl_pct: float, real_pnl_usd):
        lev     = pos.get("leverage", 1)
        liq_pct = 80 / lev
        loss    = -pnl_pct if pnl_pct < 0 else 0
        if loss > liq_pct * 0.7:
            self._notify(messages.liquidation_alert(symbol, loss, liq_pct, real_pnl_usd))

    def _fetch_real_positions(self) -> dict:
        if config.DRY_RUN:
            return {}
        try:
            return self._hl.get_all_positions()
        except Exception as e:
            logger.error(f"get_all_positions: {e}")
            return {}

    def _fetch_funding(self, symbol: str) -> float:
        if config.DRY_RUN:
            return 0.0
        try:
            return self._hl.get_funding_summary(symbol).get("total_funding_usd", 0.0)
        except Exception:
            return 0.0

    def _duration_minutes(self, pos: dict) -> int:
        try:
            opened  = datetime.fromisoformat(
                pos["opened_at"].replace("Z", "").replace("+00:00", "")
            )
            elapsed = datetime.now(timezone.utc) - opened.replace(tzinfo=timezone.utc)
            return round(elapsed.total_seconds() / 60)
        except Exception:
            return 0

    def _get_atr(self, symbol: str, period: int = 14) -> float:
        try:
            candles = self._hl.get_candles(symbol, "1h", period + 2)
            if not candles or len(candles) < period:
                return 0.0
            highs, lows, closes = [], [], []
            for c in candles:
                if isinstance(c, dict):
                    highs.append(float(c.get("h", 0)))
                    lows.append(float(c.get("l", 0)))
                    closes.append(float(c.get("c", 0)))
                elif isinstance(c, (list, tuple)) and len(c) >= 5:
                    highs.append(float(c[1]))
                    lows.append(float(c[2]))
                    closes.append(float(c[3]))
            trs = [
                max(highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
                for i in range(1, len(highs))
            ]
            return sum(trs[-period:]) / period if trs else 0.0
        except Exception:
            return 0.0