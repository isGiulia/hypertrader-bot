"""
core/trade_executor.py — Execução de trades na exchange.

Princípio Clean Code: "Funções devem fazer uma coisa e fazê-la bem."
Cada método aqui tem uma responsabilidade clara e nomeada.

V2 — Stop Loss com retry automático (evita posição exposta por falha de rede).
"""
import time
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from hypertrader import config
from hypertrader.exchange.hyperliquid_client import HyperliquidClient
from hypertrader.notifications import messages

logger = logging.getLogger("TradeExecutor")


class TradeExecutor:
    """
    Responsável por executar trades na exchange:
      - Enviar ordens de entrada
      - Colocar Stop Loss e Take Profits
      - Fechar posições (por SL, TP ou comando manual)

    Não conhece Telegram, Claude ou estado persistido — apenas executa.
    """

    def __init__(self, exchange: HyperliquidClient, notify_fn: Callable):
        self._hl     = exchange
        self._notify = notify_fn

    # ── Ordem de entrada

    def open_position(self, symbol: str, direction: str, size: float,
                      entry_price: float, leverage: int) -> dict:
        """
        Envia a ordem de entrada.
        Retorna dict com status, order_id e preço real de execução.
        """
        is_buy = direction == "LONG"
        self._hl.set_leverage(symbol, leverage, is_cross=False)

        cur_price  = self._hl.get_mark_price(symbol)
        diff_pct   = abs(entry_price - cur_price) / cur_price * 100 if cur_price > 0 else 99
        use_market = diff_pct <= config.MARKET_ORDER_THRESHOLD

        exec_price = cur_price * (1.001 if is_buy else 0.999) if use_market else entry_price
        order_type = "market" if use_market else "limit"

        logger.info(f"{symbol}: {order_type} order diff={diff_pct:.3f}%")

        result = self._hl.place_order(
            symbol=symbol, is_buy=is_buy, size=size,
            price=exec_price, order_type=order_type,
        )

        if result.get("status") != "ok":
            self._notify(
                f"⚠️ <b>ORDEM REJEITADA — {symbol}</b>\n"
                f"<code>{str(result.get('raw', ''))[:200]}</code>"
            )
            return {"status": "rejected", "reason": str(result)}

        order_id = result.get("order_id")

        # Fill imediato retornado pelo SDK
        if result.get("filled") and result.get("avg_px", 0) > 0:
            real_entry = result["avg_px"]
            logger.info(f"Fill imediato: avgPx={real_entry}")
            return {"status": "filled", "order_id": order_id, "real_entry": real_entry}

        # Aguarda e verifica se foi preenchida
        time.sleep(2)
        open_oids = [str(o.get("oid", "")) for o in self._hl.get_open_orders()]
        if str(order_id) in open_oids:
            return {"status": "pending", "order_id": order_id, "exec_price": exec_price}

        # Ordem preenchida — busca preço real via userFills
        real_entry = self._get_real_fill_price(symbol, exec_price)
        return {"status": "filled", "order_id": order_id, "real_entry": real_entry}

    # ── SL / TP

    def place_protection_orders(self, symbol: str, pos: dict,
                                 size: float, is_buy: bool,
                                 sl: float, tp1: float, tp2: float) -> dict:
        """
        Coloca Stop Loss e dois Take Profits na exchange.
        Retorna o pos atualizado com os order IDs.
        """
        sl, tp1, tp2 = float(sl), float(tp1), float(tp2)
        size         = float(size)

        pos["sl_order_id"]  = self._place_stop_loss(symbol, is_buy, size, sl)
        pos["tp1_order_id"] = self._place_take_profit(symbol, is_buy, size * config.PARTIAL_TP_PCT, tp1, "TP1")
        pos["tp2_order_id"] = self._place_take_profit(symbol, is_buy, size * (1 - config.PARTIAL_TP_PCT), tp2, "TP2")
        return pos

    def move_sl_to_breakeven(self, symbol: str, pos: dict, is_long: bool) -> dict:
        """Cancela SL atual e coloca novo SL no preço de entrada (breakeven)."""
        entry = pos.get("entry", 0)
        if pos.get("sl_order_id"):
            try:
                self._hl.cancel_order(symbol, pos["sl_order_id"])
            except Exception as e:
                logger.error(f"Cancelar SL breakeven {symbol}: {e}")

        result = self._hl.place_order(
            symbol=symbol, is_buy=is_long,
            size=pos["size"], price=entry,
            order_type="stop", reduce_only=True
        )
        pos["sl_order_id"] = result.get("order_id")
        return pos

    def update_trailing_sl(self, symbol: str, pos: dict,
                            is_long: bool, new_sl: float) -> dict:
        """Cancela SL atual e coloca novo SL trailing."""
        if pos.get("sl_order_id"):
            try:
                self._hl.cancel_order(symbol, pos["sl_order_id"])
            except Exception as e:
                logger.error(f"Cancelar trailing SL {symbol}: {e}")

        result = self._hl.place_order(
            symbol=symbol, is_buy=is_long,
            size=pos["size"], price=new_sl,
            order_type="stop", reduce_only=True
        )
        pos["sl_order_id"] = result.get("order_id")
        return pos

    # ── Fechamento

    def close_position(self, symbol: str, pos: dict) -> tuple[float, float, float]:
        """
        Fecha posição na exchange, cancela ordens pendentes e busca dados reais do fill.
        Retorna: (exit_price, real_fee, real_pnl)
        """
        is_long = pos["direction"] == "LONG"
        entry   = float(pos.get("entry", 0))

        # Cancela SL/TP restantes
        for key in ("sl_order_id", "tp1_order_id", "tp2_order_id"):
            oid = pos.get(key)
            if oid:
                try:
                    self._hl.cancel_order(symbol, oid)
                except Exception as e:
                    logger.error(f"Cancelar {key} {symbol}: {e}")

        # Envia ordem de fechamento
        try:
            self._hl.close_position(symbol, pos["size"], is_long)
        except Exception as e:
            logger.error(f"close_position {symbol}: {e}")

        # Busca dados reais via userFills
        time.sleep(1)
        return self._get_close_fill_data(symbol, entry, is_long)

    # ── Helpers privados

    def _place_stop_loss(self, symbol: str, is_buy: bool,
                          size: float, price: float,
                          max_attempts: int = 3) -> Optional[int]:
        """
        Coloca Stop Loss com retry automático.

        Estratégia:
          - Tenta até `max_attempts` vezes (default: 3).
          - Backoff exponencial entre tentativas: 1s, 2s, 4s...
          - Se TODAS falharem, notifica o usuário. O PositionMonitor
            vai tentar novamente no próximo ciclo via `_ensure_stop_loss`
            (a cada 30s), então a posição não fica permanentemente
            desprotegida — apenas por alguns segundos.

        Args:
            symbol: ativo (BTC, ETH, ...)
            is_buy: True se posição LONG (SL fecha com SELL), False se SHORT
            size: tamanho em contratos
            price: preço de trigger do SL
            max_attempts: tentativas máximas antes de desistir

        Returns:
            order_id se sucesso, None se todas as tentativas falharem.
        """
        closing_is_buy = not is_buy   # LONG → fecha com SELL | SHORT → fecha com BUY
        last_error     = None

        for attempt in range(1, max_attempts + 1):
            try:
                result = self._hl.place_order(
                    symbol=symbol,
                    is_buy=closing_is_buy,
                    size=size,
                    price=price,
                    order_type="stop",
                    reduce_only=True,
                )
                oid = result.get("order_id")

                if result.get("status") == "ok" and oid:
                    if attempt > 1:
                        logger.info(f"SL [{symbol}] OK na tentativa {attempt}/{max_attempts}")
                    logger.info(f"SL [{symbol}] @ ${price:.4f} oid={oid}")
                    return oid

                last_error = f"API respondeu sem order_id: {str(result)[:150]}"
                logger.warning(f"SL [{symbol}] tentativa {attempt}/{max_attempts}: {last_error}")

            except Exception as e:
                last_error = str(e)
                logger.warning(f"SL [{symbol}] tentativa {attempt}/{max_attempts} exception: {e}")

            # Backoff exponencial (só espera se ainda houver tentativas)
            if attempt < max_attempts:
                wait = 2 ** (attempt - 1)   # 1s, 2s, 4s...
                time.sleep(wait)

        # Todas as tentativas falharam
        logger.error(f"SL [{symbol}] FALHOU após {max_attempts} tentativas: {last_error}")
        self._notify(
            f"⚠️ <b>SL falhou {max_attempts}x — {symbol}</b>\n"
            f"Posição <b>temporariamente sem proteção</b>.\n"
            f"O monitor tentará recolocar em até 30s.\n"
            f"Erro: <code>{str(last_error)[:120]}</code>"
        )
        return None

    def _place_take_profit(self, symbol: str, is_buy: bool,
                            size: float, price: float, label: str) -> Optional[int]:
        try:
            result = self._hl.place_order(
                symbol=symbol, is_buy=not is_buy,
                size=size, price=price,
                order_type="limit", reduce_only=True,
            )
            oid = result.get("order_id")
            logger.info(f"{label} [{symbol}] @ ${price:.4f} oid={oid}")
            return oid
        except Exception as e:
            logger.error(f"{label} [{symbol}]: {e}")
            return None

    def _get_real_fill_price(self, symbol: str, fallback: float) -> float:
        try:
            last = self._hl.get_last_fill(symbol)
            if last and last.get("price", 0) > 0:
                price = last["price"]
                logger.info(f"Fill via userFills: px={price} fee=${last.get('fee', 0)}")
                return price
        except Exception as e:
            logger.error(f"get_real_fill_price {symbol}: {e}")
        return fallback

    def _get_close_fill_data(self, symbol: str, entry: float,
                              is_long: bool) -> tuple[float, float, float]:
        """Retorna (exit_price, fee, closed_pnl) do último fill de fechamento."""
        try:
            last = self._hl.get_last_fill(symbol)
            if last and last.get("price", 0) > 0:
                exit_price = last["price"]
                real_fee   = last.get("fee", 0.0)
                closed_pnl = last.get("closed_pnl", 0.0)
                logger.info(f"Close fill: px={exit_price} fee=${real_fee} pnl=${closed_pnl}")
                return exit_price, real_fee, closed_pnl
        except Exception as e:
            logger.error(f"_get_close_fill_data {symbol}: {e}")

        mark = self._hl.get_mark_price(symbol)
        return mark, 0.0, 0.0