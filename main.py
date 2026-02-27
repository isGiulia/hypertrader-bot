#!/usr/bin/env python3
"""
HyperTrader AI — Motor de trading
Hyperliquid Perpetuals | Claude AI
"""

import os
import re
import json
import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Optional
import anthropic
from dotenv import load_dotenv
load_dotenv()
from hyperliquid_client import HyperliquidClient
from market_analyzer import MarketAnalyzer
from risk_manager import RiskManager

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/hypertrader.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("HyperTrader")

STATE_FILE = "state.json"

CONFIG = {
    "anthropic_api_key":  os.getenv("ANTHROPIC_API_KEY", ""),
    "hl_wallet_address":  os.getenv("HL_WALLET_ADDRESS", ""),
    "hl_private_key":     os.getenv("HL_PRIVATE_KEY", ""),
    "symbols":            ["BTC", "ETH", "SOL", "AVAX", "PEPE"],
    "max_positions":      5,
    "risk_per_trade":     0.025,
    "default_leverage":   10,
    "leverage_fallback":  20,
    "max_leverage":       50,
    "min_confidence":     75,
    "daily_loss_limit":   0.06,
    "take_profit_ratio":  2.0,
    "trailing_stop":      True,
    "partial_tp_pct":     0.50,
    "analysis_interval":  900,
    "pnl_update_interval":120,
    "use_testnet":        False,
    "dry_run":            True,
    "telegram_token":     os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id":   int(os.getenv("TELEGRAM_CHAT_ID", "0")),
    "telegram_allowed_ids": os.getenv("TELEGRAM_ALLOWED_IDS", "0"),
}

TRADER_SYSTEM_PROMPT = """Trader profissional de perpetuals na Hyperliquid. Capital real em jogo.

PASSO 1 — CONTEXTO MACRO (obrigatorio antes de qualquer sinal):
- Leia "macro_sentiment" nos dados recebidos
- Fear & Greed <= 20 (Medo Extremo): PROIBIDO LONG com confianca > 60%. Exige 5+ confluencias.
- Fear & Greed 20-40 (Medo): LONG so com 4+ confluencias. Reduza confianca em 15 pontos.
- Fear & Greed >= 80 (Ganancia Extrema): PROIBIDO LONG com confianca > 60%. Risco de topo.
- Leia "data_errors": se houver erros de API, cap confianca em 50%.
- Leia "btc_dominance": se > 55% e subindo, reduza confianca em LONG de altcoins em 20 pontos.

PASSO 2 — TENDENCIA (criterios rigorosos — todos obrigatorios):
- UPTREND 4h: preco ACIMA da MM50 E MM50 ascendente E topos/fundos subindo
- DOWNTREND 4h: preco ABAIXO da MM50 E MM50 descendente E topos/fundos caindo
- LATERAL: qualquer outro caso — nao classifique como trend sem TODOS os criterios

PASSO 3 — CONFLUENCIAS (minimo 3 para entrar, 5 para confianca >= 75%):
BOS/CHoCH, order block, FVG, RSI divergencia, volume acima media, funding favoravel,
suporte/resistencia testado multiplas vezes, padrao de vela confirmado

PASSO 4 — DECISAO:
- LONG: macro ok + uptrend confirmado + 3+ confluencias + volume presente
- SHORT: macro ok + downtrend confirmado + 3+ confluencias + volume presente
- WAIT: qualquer duvida, macro adverso, menos de 3 confluencias, volume seco

REGRA DE OURO: confianca = tecnica + macro + volume. Confianca alta (>75%) so com TUDO alinhado.

RESPONDA APENAS COM JSON puro, sem backticks, sem texto:
{"signal":"LONG","confidence":0,"entry_price":0.0,"stop_loss":0.0,"take_profit_1":0.0,"take_profit_2":0.0,"leverage":0,"timeframe":"1h","momento":"...","ganho_maximo":"...","perda_maxima":"...","reasoning_tecnico":"...","reasoning_simples":"...","invalidation":"..."}
"""


class HyperTraderAgent:

    def __init__(self):
        self.ai = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
        self.hl = HyperliquidClient(
            wallet_address=CONFIG["hl_wallet_address"],
            private_key=CONFIG["hl_private_key"],
            testnet=CONFIG["use_testnet"]
        )
        self.analyzer  = MarketAnalyzer(self.hl)
        self.risk      = RiskManager(CONFIG)

        # [3.1] Historico separado por simbolo — evita contaminacao cruzada
        self.history: dict = {}

        self.active_positions   = {}
        self.trade_history      = []
        self.daily_pnl          = 0.0
        self.session_pnl        = 0.0
        self.paused             = False
        self.session_start      = datetime.now(timezone.utc)
        self.telegram_bot       = None
        self.stats              = {"total": 0, "wins": 0, "losses": 0, "best": 0.0, "worst": 0.0}
        self.asset_leverage_map: dict = {}
        self._last_weak_signal: dict  = {}
        self._balance_check_count     = 0
        self._current_day             = datetime.now(timezone.utc).date()

        # [1.4] Carregar estado persistido
        self._load_state()
        self._load_leverage_limits()

        logger.info("HyperTrader AI iniciado")
        logger.info(f"Simbolos: {CONFIG['symbols']}")
        logger.info(f"Modo: {'TESTNET' if CONFIG['use_testnet'] else 'MAINNET'} | {'DRY RUN' if CONFIG['dry_run'] else 'LIVE'}")

    # ── [1.4] Persistencia de estado ─────────────────────────────────────────

    def _save_state(self):
        """Salva estado em state.json — chamado a cada abertura/fechamento de posicao"""
        try:
            state = {
                "active_positions": self.active_positions,
                "trade_history":    self.trade_history[-100:],
                "stats":            self.stats,
                "session_pnl":      self.session_pnl,
                "daily_pnl":        self.daily_pnl,
                "saved_at":         datetime.now(timezone.utc).isoformat()
            }
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, default=str)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            logger.error(f"Erro ao salvar state: {e}")

    def _load_state(self):
        """Carrega estado do state.json se existir"""
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            self.active_positions = state.get("active_positions", {})
            self.trade_history    = state.get("trade_history", [])
            self.stats            = state.get("stats", self.stats)
            self.session_pnl      = state.get("session_pnl", 0.0)
            self.daily_pnl        = state.get("daily_pnl", 0.0)
            count = len(self.active_positions)
            logger.info(f"Estado carregado: {count} posicoes abertas, P&L=${self.session_pnl:+.2f}")
            if count > 0:
                logger.warning(f"Posicoes restauradas: {list(self.active_positions.keys())}")
        except Exception as e:
            logger.error(f"Erro ao carregar state: {e}")

    # ── Leverage ─────────────────────────────────────────────────────────────

    def _load_leverage_limits(self):
        try:
            info = self.hl.get_asset_info()
            if not info:
                return
            for sym in CONFIG["symbols"]:
                self.asset_leverage_map[sym] = info.get(sym, {}).get("maxLeverage", CONFIG["leverage_fallback"])
        except Exception as e:
            logger.error(f"Erro leverage: {e}")

    def get_symbol_max_leverage(self, symbol: str) -> int:
        if symbol in self.asset_leverage_map:
            return self.asset_leverage_map[symbol]
        try:
            lev = self.hl.get_max_leverage(symbol)
            self.asset_leverage_map[symbol] = lev
            return lev
        except Exception:
            return CONFIG["leverage_fallback"]

    # ── LLM — historico separado por simbolo ─────────────────────────────────

    def _chat(self, prompt: str, ctx: dict = None, symbol: str = "_default") -> str:
        """Chat com historico separado por simbolo — evita contaminacao cruzada"""
        msg = prompt
        if ctx:
            msg += "\n\n" + json.dumps(ctx, indent=2, default=str)
        hist = self.history.setdefault(symbol, [])
        hist.append({"role": "user", "content": msg})

        # [2.4] Modelo sonnet para melhor qualidade de raciocinio
        resp = self.ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=TRADER_SYSTEM_PROMPT,
            messages=hist
        )
        answer = resp.content[0].text
        hist.append({"role": "assistant", "content": answer})

        # Limitar historico a 20 mensagens por simbolo
        if len(hist) > 20:
            self.history[symbol] = hist[-20:]
        return answer

    def _extract_signal(self, text: str) -> Optional[dict]:
        """Extrai JSON do sinal — tenta varios formatos"""
        # Tenta ```json ... ```
        try:
            for m in re.findall(r"```json(.*?)```", text, re.DOTALL):
                d = json.loads(m.strip())
                if "signal" in d:
                    return d
        except Exception:
            pass
        # Tenta ``` ... ```
        try:
            for m in re.findall(r"```(.*?)```", text, re.DOTALL):
                d = json.loads(m.strip())
                if "signal" in d:
                    return d
        except Exception:
            pass
        # JSON direto
        try:
            d = json.loads(text.strip())
            if "signal" in d:
                return d
        except Exception:
            pass
        # Maior bloco {} no texto
        try:
            start = text.find("{")
            end   = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                d = json.loads(text[start:end+1])
                if "signal" in d:
                    return d
        except Exception:
            pass
        logger.warning("Sinal nao extraido: " + text[:150])
        return None

    # ── Analise ───────────────────────────────────────────────────────────────

    def analyze_market(self, symbol: str) -> dict:
        logger.info(f"Analisando {symbol}...")
        now       = datetime.now(timezone.utc).timestamp()
        cache_age = now - getattr(self, "_4h_cache_time", {}).get(symbol, 0)

        if not hasattr(self, "_4h_cache"):
            self._4h_cache      = {}
            self._4h_cache_time = {}

        if cache_age > 14400:
            data = self.analyzer.get_full_analysis(symbol)
            self._4h_cache[symbol] = {
                "market_structure": data.get("market_structure", {}),
                "funding":          data.get("funding", {}),
                "macro_sentiment":  data.get("macro_sentiment", {}),
                "indicators":       {"4h": data.get("indicators", {}).get("4h", {})},
            }
            self._4h_cache_time[symbol] = now
        else:
            data = self.analyzer.get_full_analysis(symbol)
            data["market_structure"] = self._4h_cache.get(symbol, {}).get("market_structure", {})
            data["macro_sentiment"]  = self._4h_cache.get(symbol, {}).get("macro_sentiment", data.get("macro_sentiment", {}))

        platform_max = self.get_symbol_max_leverage(symbol)
        prompt = (
            f"{symbol} perp Hyperliquid. Max leverage disponivel: {platform_max}x.\n"
            f"Analise D1+4h+1h. Retorne APENAS o JSON."
        )
        analysis = self._chat(prompt, data, symbol=symbol)
        signal   = self._extract_signal(analysis)
        if signal:
            signal["symbol"] = symbol
            logger.info(f"{symbol}: {signal.get('signal')} conf={signal.get('confidence')}%")
        else:
            logger.warning(f"{symbol}: nao foi possivel extrair sinal")
        return {"symbol": symbol, "analysis": analysis, "signal": signal}

    # ── Execucao ──────────────────────────────────────────────────────────────

    def execute_trade(self, symbol: str, signal: dict) -> dict:
        if not signal or signal.get("signal") == "WAIT":
            return {"status": "skipped"}

        direction    = signal["signal"]
        entry        = float(signal.get("entry_price", 0))
        sl           = float(signal.get("stop_loss", 0))
        tp1          = float(signal.get("take_profit_1", 0))
        tp2          = float(signal.get("take_profit_2", 0))
        confidence   = signal.get("confidence", 0)
        platform_max = self.get_symbol_max_leverage(symbol)
        leverage     = min(int(signal.get("leverage", platform_max)), platform_max)
        simple       = signal.get("reasoning_simples", signal.get("reasoning", ""))
        ganho        = signal.get("ganho_maximo", "")
        perda        = signal.get("perda_maxima", "")

        val = self.risk.validate_trade(signal, self.active_positions, self.daily_pnl)
        if not val["approved"]:
            logger.warning(f"Bloqueado [{symbol}]: {val['reason']}")
            return {"status": "blocked", "reason": val["reason"]}

        acc    = self.hl.get_account_info()
        equity = acc.get("equity", 1000)
        size   = self.risk.calculate_position_size(equity, entry, sl)

        risk_pct   = abs(entry - sl)   / entry * 100 if entry > 0 else 0
        tp1_pct    = abs(tp1 - entry)  / entry * 100 if entry > 0 else 0
        tp2_pct    = abs(tp2 - entry)  / entry * 100 if entry > 0 else 0
        risk_usd   = abs(entry - sl)   / entry * equity if entry > 0 else 0
        reward_usd = abs(tp1 - entry)  / entry * equity if entry > 0 else 0
        rr_ratio   = round(reward_usd / risk_usd, 2) if risk_usd > 0 else 0

        pos = {
            "direction":   direction,
            "entry":       entry,
            "stop_loss":   sl,
            "tp1":         tp1,
            "tp2":         tp2,
            "size":        size,
            "leverage":    leverage,
            "equity_open": equity,
            "opened_at":   datetime.now(timezone.utc).isoformat(),
            "peak_pnl":    0.0,
            "tp1_hit":     False,
            "tp1_order_id": None,
            "tp2_order_id": None,
            "sl_order_id":  None,
        }

        is_buy = direction == "LONG"

        if CONFIG["dry_run"]:
            self.active_positions[symbol] = pos
            self.stats["total"] += 1
        else:
            try:
                # [1.3] is_cross=False — sempre isolated margin
                self.hl.set_leverage(symbol, leverage, is_cross=False)

                # Ordem de entrada
                entry_result = self.hl.place_order(
                    symbol=symbol, is_buy=is_buy, size=size,
                    price=entry, order_type="limit"
                )
                pos["entry_order_id"] = entry_result.get("order_id")

                # [2.2] Ordens de SL, TP1 e TP2 na exchange imediatamente
                sl_result = self.hl.place_order(
                    symbol=symbol, is_buy=not is_buy, size=size,
                    price=sl, order_type="stop", reduce_only=True
                )
                pos["sl_order_id"] = sl_result.get("order_id")

                tp1_size   = size * CONFIG["partial_tp_pct"]
                tp1_result = self.hl.place_order(
                    symbol=symbol, is_buy=not is_buy, size=tp1_size,
                    price=tp1, order_type="limit", reduce_only=True
                )
                pos["tp1_order_id"] = tp1_result.get("order_id")

                tp2_size   = size * (1 - CONFIG["partial_tp_pct"])
                tp2_result = self.hl.place_order(
                    symbol=symbol, is_buy=not is_buy, size=tp2_size,
                    price=tp2, order_type="limit", reduce_only=True
                )
                pos["tp2_order_id"] = tp2_result.get("order_id")

                self.active_positions[symbol] = pos
                self.stats["total"] += 1
                logger.info(f"{direction} {symbol} @ ${entry:,.4f} | SL={sl_result.get('order_id')} TP1={tp1_result.get('order_id')} TP2={tp2_result.get('order_id')}")
            except Exception as e:
                logger.error(f"Erro execucao {symbol}: {e}")
                return {"status": "error", "error": str(e)}

        # [1.4] Salvar estado apos abrir posicao
        self._save_state()

        em = "🟢" if direction == "LONG" else "🔴"
        msg = (
            em + " <b>ENTRADA — " + direction + "</b>\n"
            "────────────────────\n"
            "Par: <code>" + symbol + "/USDC</code>\n"
            "Alavancagem: <code>" + str(leverage) + "x</code> | Confianca: <code>" + str(confidence) + "%</code>\n\n"
            "💵 Entrada: <code>$" + f"{entry:,.4f}" + "</code>\n"
            "🛑 Stop Loss: <code>$" + f"{sl:,.4f}" + "</code> (-" + f"{risk_pct:.2f}" + "%)\n"
            "🎯 TP1: <code>$" + f"{tp1:,.4f}" + "</code> (+" + f"{tp1_pct:.2f}" + "%)\n"
            "🏆 TP2: <code>$" + f"{tp2:,.4f}" + "</code> (+" + f"{tp2_pct:.2f}" + "%)\n\n"
            "📐 R:R: <code>1:" + str(rr_ratio) + "</code>\n"
        )
        if ganho:
            msg += "💰 Ganho maximo: <code>" + str(ganho) + "</code>\n"
        if perda:
            msg += "⚠️ Perda maxima: <code>" + str(perda) + "</code>\n"
        if simple:
            msg += "\n💬 " + str(simple) + "\n"
        msg += "\n🕐 " + datetime.now(timezone.utc).strftime("%H:%M UTC")
        msg += " | " + ("DRY RUN" if CONFIG["dry_run"] else "LIVE")
        self._notify(msg)

        return {"status": "dry_run" if CONFIG["dry_run"] else "executed"}

    # ── Monitor posicoes ──────────────────────────────────────────────────────

    def monitor_positions(self):
        for symbol, pos in list(self.active_positions.items()):
            current = self.hl.get_mark_price(symbol)
            if current == 0:
                continue
            is_long = pos["direction"] == "LONG"
            pnl_pct = ((current - pos["entry"]) / pos["entry"] * 100) if is_long \
                      else ((pos["entry"] - current) / pos["entry"] * 100)
            pos["peak_pnl"] = max(pos["peak_pnl"], pnl_pct)

            hit_sl  = (is_long and current <= pos["stop_loss"]) or \
                      (not is_long and current >= pos["stop_loss"])
            hit_tp1 = (not pos["tp1_hit"]) and (
                      (is_long and current >= pos["tp1"]) or
                      (not is_long and current <= pos["tp1"]))
            hit_tp2 = pos["tp1_hit"] and (
                      (is_long and current >= pos["tp2"]) or
                      (not is_long and current <= pos["tp2"]))

            if hit_sl:
                self._close_position(symbol, pos, current, "Stop Loss atingido")
            elif hit_tp2:
                self._close_position(symbol, pos, current, "TP2 atingido — Saida total")
            elif hit_tp1:
                partial = pos["size"] * CONFIG["partial_tp_pct"]
                if not CONFIG["dry_run"]:
                    self.hl.place_order(symbol=symbol, is_buy=not is_long,
                                        size=partial, price=current,
                                        order_type="market", reduce_only=True)
                pos["size"]     -= partial
                pos["stop_loss"] = pos["entry"]   # break even
                pos["tp1_hit"]   = True
                lev_pnl = pnl_pct * pos["leverage"]
                self._notify(
                    "🎯 <b>TP1 ATINGIDO — " + symbol + "</b>\n"
                    "Preco: <code>$" + f"{current:,.4f}" + "</code> | Lucro: <code>+" + f"{lev_pnl:.2f}" + "%</code>\n"
                    "50% do lucro realizado!\n"
                    "Stop movido para entrada — sem risco de perda agora!\n"
                    "Aguardando TP2: <code>$" + f"{pos['tp2']:,.4f}" + "</code>"
                )
                self._save_state()

            # [3.3] Trailing stop real para posicoes onde TP1 ja foi atingido
            elif pos["tp1_hit"] and CONFIG.get("trailing_stop"):
                atr = self._get_atr(symbol)
                if atr > 0:
                    new_sl = self.risk.calculate_trailing_stop(current, pos["direction"], atr)
                    if pos["direction"] == "LONG" and new_sl > pos["stop_loss"]:
                        pos["stop_loss"] = new_sl
                        logger.info(f"Trailing stop {symbol} atualizado: ${new_sl:,.4f}")
                    elif pos["direction"] == "SHORT" and new_sl < pos["stop_loss"]:
                        pos["stop_loss"] = new_sl
                        logger.info(f"Trailing stop {symbol} atualizado: ${new_sl:,.4f}")

    def _get_atr(self, symbol: str, period: int = 14) -> float:
        """Calcula ATR simples para trailing stop"""
        try:
            candles = self.hl.get_candles(symbol, "1h", period + 1)
            if not candles or len(candles) < period:
                return 0
            highs  = [float(c.get("h", c[2])) if isinstance(c, (list, tuple)) else float(c.get("h", 0)) for c in candles]
            lows   = [float(c.get("l", c[3])) if isinstance(c, (list, tuple)) else float(c.get("l", 0)) for c in candles]
            closes = [float(c.get("c", c[4])) if isinstance(c, (list, tuple)) else float(c.get("c", 0)) for c in candles]
            trs = []
            for i in range(1, len(candles)):
                tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                trs.append(tr)
            return sum(trs[-period:]) / period
        except Exception:
            return 0

    def _close_position(self, symbol: str, pos: dict, exit_price: float, reason: str):
        is_long  = pos["direction"] == "LONG"
        pnl_pct  = ((exit_price - pos["entry"]) / pos["entry"] * 100) if is_long \
                   else ((pos["entry"] - exit_price) / pos["entry"] * 100)
        pnl_lev  = pnl_pct * pos["leverage"]
        pnl_usd  = (pnl_pct / 100) * pos["equity_open"]
        dur_min  = round((datetime.now(timezone.utc) -
                          datetime.fromisoformat(
                              pos["opened_at"].replace("Z","").replace("+00:00","")
                          )).total_seconds() / 60)

        if not CONFIG["dry_run"]:
            # Cancelar ordens abertas antes de fechar
            for oid_key in ("sl_order_id", "tp1_order_id", "tp2_order_id"):
                oid = pos.get(oid_key)
                if oid:
                    try:
                        self.hl.cancel_order(symbol, oid)
                    except Exception:
                        pass
            self.hl.close_position(symbol, pos["size"], is_long)

        em = "✅" if pnl_usd >= 0 else "❌"
        self._notify(
            em + " <b>SAIDA — " + ("LUCRO" if pnl_usd >= 0 else "PERDA") + "</b>\n"
            "────────────────────\n"
            "Par: <code>" + symbol + "/USDC</code> | " + pos["direction"] + "\n"
            "Motivo: " + reason + "\n"
            "Duracao: " + str(dur_min) + " minutos\n\n"
            "Entrada: <code>$" + f"{pos['entry']:,.4f}" + "</code>\n"
            "Saida: <code>$" + f"{exit_price:,.4f}" + "</code>\n"
            "Alavancagem: <code>" + str(pos["leverage"]) + "x</code>\n\n"
            "<b>Resultado: <code>" + f"{pnl_lev:+.2f}" + "%</code> (<code>$" + f"{pnl_usd:+.2f}" + "</code>)</b>\n"
            "────────────────────\n"
            "🕐 " + datetime.now(timezone.utc).strftime("%H:%M UTC")
        )

        self.session_pnl += pnl_usd
        self.daily_pnl   += pnl_usd
        if pnl_usd >= 0:
            self.stats["wins"] += 1
            self.stats["best"] = max(self.stats["best"], pnl_usd)
        else:
            self.stats["losses"] += 1
            self.stats["worst"]  = min(self.stats["worst"], pnl_usd)

        self.trade_history.append({**pos, "symbol": symbol, "exit": exit_price,
                                   "exit_reason": reason, "pnl_usd": pnl_usd})
        del self.active_positions[symbol]

        # [1.4] Salvar estado apos fechar posicao
        self._save_state()

    # ── Update P&L ────────────────────────────────────────────────────────────

    def notify_pnl_update(self):
        if not self.active_positions:
            return
        total      = self.stats["total"]
        wr         = round(self.stats["wins"] / total * 100, 1) if total > 0 else 0.0
        unrealized = 0.0
        lines      = ["📊 <b>UPDATE — " + datetime.now(timezone.utc).strftime("%H:%M UTC") + "</b>", "────────────────────"]
        for sym, pos in self.active_positions.items():
            cur  = self.hl.get_mark_price(sym)
            il   = pos["direction"] == "LONG"
            pp   = ((cur - pos["entry"]) / pos["entry"] * 100) if il else ((pos["entry"] - cur) / pos["entry"] * 100)
            pl   = pp * pos["leverage"]
            pu   = (pp / 100) * pos["equity_open"]
            unrealized += pu
            em   = "🟢" if pu >= 0 else "🔴"
            lines.append(
                em + " <b>" + sym + "</b> " + ("LONG" if il else "SHORT") + " " + str(pos["leverage"]) + "x\n"
                "  <code>$" + f"{pos['entry']:,.2f}" + "</code> → <code>$" + f"{cur:,.2f}" + "</code>\n"
                "  P&amp;L: <code>" + f"{pl:+.2f}" + "%</code> (<code>$" + f"{pu:+.2f}" + "</code>)"
            )
        lines += [
            "────────────────────",
            "Nao realizado: <code>$" + f"{unrealized:+.2f}" + "</code>",
            "Realizado hoje: <code>$" + f"{self.session_pnl:+.2f}" + "</code>",
            "Win Rate: <code>" + str(wr) + "%</code> (" + str(self.stats["wins"]) + "W/" + str(self.stats["losses"]) + "L)",
        ]
        self._notify("\n".join(lines))

    def generate_daily_report(self) -> str:
        total = self.stats["total"]
        wr    = round(self.stats["wins"] / total * 100, 1) if total > 0 else 0
        ctx   = {
            "session_pnl":  self.session_pnl,
            "total_trades": total,
            "win_rate":     f"{wr}%",
            "wins":         self.stats["wins"],
            "losses":       self.stats["losses"],
            "best_trade":   self.stats["best"],
            "worst_trade":  self.stats["worst"],
        }
        return self._chat(
            "Gere um relatorio executivo da sessao em portugues simples.",
            ctx, symbol="_report"
        )

    # ── Notificacao ───────────────────────────────────────────────────────────

    def _notify(self, msg: str):
        if self.telegram_bot and CONFIG["telegram_chat_id"]:
            try:
                loop = asyncio.get_event_loop()
                if loop and loop.is_running():
                    asyncio.ensure_future(
                        self.telegram_bot.notify(CONFIG["telegram_chat_id"], msg)
                    )
                else:
                    loop.run_until_complete(
                        self.telegram_bot.notify(CONFIG["telegram_chat_id"], msg)
                    )
            except Exception as e:
                logger.error(f"Notify error: {e}")

    # ═════════════════════════════════════════════════════════════════════════
    #  LOOP AUTONOMO
    # ═════════════════════════════════════════════════════════════════════════

    async def run_autonomous(self):
        logger.info("Loop autonomo iniciado")
        self._notify(
            "🚀 <b>HyperTrader ATIVO</b>\n"
            "────────────────────\n"
            "🌐 " + ("TESTNET" if CONFIG["use_testnet"] else "MAINNET") + "\n"
            "⚙️ " + ("DRY RUN — Simulado" if CONFIG["dry_run"] else "🔴 LIVE — Dinheiro real") + "\n"
            "🎯 Pares: <code>" + ", ".join(CONFIG["symbols"]) + "</code>\n"
            "📊 Max posicoes: <code>" + str(CONFIG["max_positions"]) + "</code>\n"
            "⏱ Analise a cada: <code>" + str(CONFIG["analysis_interval"]) + "s</code>"
        )

        pnl_tick = 0

        while True:
            try:
                if self.paused:
                    await asyncio.sleep(30)
                    continue

                # [2.1] Reset daily_pnl automatico a meia-noite UTC
                today = datetime.now(timezone.utc).date()
                if today != self._current_day:
                    old_pnl            = self.daily_pnl
                    self.daily_pnl     = 0.0
                    self._current_day  = today
                    self._notify(
                        "🌅 <b>Novo dia — P&amp;L diario resetado</b>\n"
                        "P&amp;L do dia anterior: <code>$" + f"{old_pnl:+.2f}" + "</code>\n"
                        "Iniciando novo dia zerado."
                    )
                    logger.info(f"Daily PnL resetado. Ontem: ${old_pnl:+.2f}")

                # Stop diario
                acc_val = self.hl.get_account_info().get("equity", 10000)
                daily_limit_usd = acc_val * CONFIG["daily_loss_limit"]
                if self.daily_pnl < 0 and abs(self.daily_pnl) >= daily_limit_usd:
                    self._notify(
                        "⛔ <b>STOP DIARIO ATINGIDO</b>\n"
                        "Perda: <code>$" + f"{self.daily_pnl:.2f}" + "</code>\n"
                        "Limite: <code>$" + f"{daily_limit_usd:.2f}" + "</code>\n"
                        "Bot pausado por 2h automaticamente."
                    )
                    await asyncio.sleep(7200)
                    self.daily_pnl = 0.0
                    continue

                # Monitorar posicoes
                self.monitor_positions()

                # Update P&L periodico
                pnl_tick += 10
                if pnl_tick >= CONFIG["pnl_update_interval"]:
                    self.notify_pnl_update()
                    pnl_tick = 0

                # Analisar todas as moedas e notificar CADA resultado
                resultados   = []
                entrou_trade = False

                for sym in CONFIG["symbols"]:
                    if self.paused:
                        break
                    if sym in self.active_positions:
                        pos = self.active_positions[sym]
                        cur = self.hl.get_mark_price(sym)
                        il  = pos["direction"] == "LONG"
                        pp  = ((cur - pos["entry"]) / pos["entry"] * 100) if il else ((pos["entry"] - cur) / pos["entry"] * 100)
                        pl  = pp * pos["leverage"]
                        pu  = (pp / 100) * pos["equity_open"]
                        em  = "🟢" if pu >= 0 else "🔴"
                        resultados.append(em + " <b>" + sym + "</b> em posicao | P&amp;L: <code>" + f"{pl:+.2f}" + "%</code>")
                        continue

                    if len(self.active_positions) >= CONFIG["max_positions"]:
                        resultados.append("⏸ <b>" + sym + "</b> — aguardando vaga (max posicoes)")
                        continue

                    r    = self.analyze_market(sym)
                    s    = r.get("signal")
                    sig  = s.get("signal", "WAIT") if s else "WAIT"
                    conf = s.get("confidence", 0) if s else 0
                    momento = s.get("momento", "") if s else ""

                    if sig == "LONG":
                        em = "🟢"; dir_pt = "COMPRAR"
                    elif sig == "SHORT":
                        em = "🔴"; dir_pt = "VENDER"
                    else:
                        em = "⚪"; dir_pt = "ESPERAR"

                    linha = em + " <b>" + sym + "</b> — " + dir_pt + " <code>" + str(conf) + "%</code>"
                    if momento:
                        linha += "\n    <i>" + str(momento)[:100] + "</i>"
                    resultados.append(linha)

                    if sig in ("LONG", "SHORT") and conf >= CONFIG["min_confidence"]:
                        self.execute_trade(sym, s)
                        entrou_trade = True
                    elif sig in ("LONG", "SHORT") and conf < CONFIG["min_confidence"]:
                        now_ts = datetime.now(timezone.utc).timestamp()
                        if now_ts - self._last_weak_signal.get(sym, 0) > 3600:
                            self._last_weak_signal[sym] = now_ts

                    await asyncio.sleep(8)

                # Resumo do ciclo
                total       = self.stats["total"]
                wr          = round(self.stats["wins"] / total * 100, 1) if total > 0 else 0
                proxima     = datetime.now(timezone.utc).timestamp() + CONFIG["analysis_interval"]
                proxima_str = datetime.fromtimestamp(proxima, tz=timezone.utc).strftime("%H:%M UTC")

                resumo = (
                    "🔄 <b>Ciclo Completo</b>\n"
                    "────────────────────\n"
                    + "\n".join(resultados) + "\n\n"
                    "────────────────────\n"
                    "📊 Posicoes: <code>" + str(len(self.active_positions)) + "/" + str(CONFIG["max_positions"]) + "</code>\n"
                    "💵 P&amp;L hoje: <code>$" + f"{self.daily_pnl:+.2f}" + "</code>\n"
                    "💵 P&amp;L sessao: <code>$" + f"{self.session_pnl:+.2f}" + "</code>\n"
                    "🏆 Win Rate: <code>" + str(wr) + "%</code> (" + str(self.stats["wins"]) + "W/" + str(self.stats["losses"]) + "L)\n"
                    "⏱ Proxima: <code>" + proxima_str + "</code>"
                )
                self._notify(resumo)
                logger.info(f"Ciclo completo | Posicoes: {len(self.active_positions)} | P&L: ${self.session_pnl:+.2f} | Proxima: {proxima_str}")
                await asyncio.sleep(CONFIG["analysis_interval"])

            except Exception as e:
                logger.error(f"Erro no loop: {e}", exc_info=True)
                await asyncio.sleep(30)


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    agent = HyperTraderAgent()
    mode  = sys.argv[1] if len(sys.argv) > 1 else "interactive"

    if mode == "auto":
        async def main_async():
            if CONFIG["telegram_token"]:
                from telegram_bot import TelegramBot
                tg = TelegramBot(agent, CONFIG["telegram_token"])
                agent.telegram_bot = tg
                logger.info("Telegram Bot ativo")
                await tg.app.initialize()
                await tg.app.start()
                await tg.app.updater.start_polling(drop_pending_updates=True)
                try:
                    await agent.run_autonomous()
                finally:
                    await tg.app.updater.stop()
                    await tg.app.stop()
                    await tg.app.shutdown()
            else:
                await agent.run_autonomous()

        asyncio.run(main_async())
    else:
        while True:
            try:
                ui = input("Voce: ").strip()
                if not ui or ui.lower() in ("sair", "exit"):
                    break
                elif ui.lower().startswith("analisar "):
                    sym = ui.split()[-1].upper()
                    r   = agent.analyze_market(sym)
                    print(f"\n{r['analysis']}\n")
                    if r["signal"] and r["signal"].get("signal") != "WAIT":
                        agent.execute_trade(sym, r["signal"])
                elif ui.lower() == "posicoes":
                    print(json.dumps(agent.active_positions, indent=2, default=str))
                else:
                    print(f"\nAgente: {agent._chat(ui, symbol='_chat')}\n")
            except KeyboardInterrupt:
                break