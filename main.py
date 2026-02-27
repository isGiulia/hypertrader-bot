#!/usr/bin/env python3
"""
HyperTrader AI — Motor de trading focado em resultados
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

CONFIG = {
    "anthropic_api_key":  os.getenv("ANTHROPIC_API_KEY", ""),
    "hl_wallet_address":  os.getenv("HL_WALLET_ADDRESS", ""),
    "hl_private_key":     os.getenv("HL_PRIVATE_KEY", ""),
    "symbols": ["BTC", "ETH", "SOL", "AVAX", "PEPE"],
    "max_positions":          5,
    "risk_per_trade":         0.025,
    "default_leverage":       10,
    "leverage_fallback":      20,
    "min_confidence":         75,
    "daily_loss_limit":       0.06,
    "take_profit_ratio":      2.0,
    "trailing_stop":          True,
    "partial_tp_pct":         0.50,
    "analysis_interval":      900,
    "pnl_update_interval":    120,
    "use_testnet":            False,
    "dry_run":                True,
    "telegram_token":       os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id":     int(os.getenv("TELEGRAM_CHAT_ID", "0")),
    "telegram_allowed_ids": os.getenv("TELEGRAM_ALLOWED_IDS", "0"),
}

TRADER_SYSTEM_PROMPT = """Trader profissional de perpetuals na Hyperliquid. Capital real em jogo.

REGRAS OBRIGATORIAS — siga em ordem:

PASSO 1 — CONTEXTO MACRO (obrigatorio antes de qualquer sinal):
- Leia o campo "macro_sentiment" nos dados: Fear & Greed Index
- Fear & Greed <= 20 (Medo Extremo): PROIBIDO dar LONG com confianca > 60%. Exige 5+ confluencias tecnicas.
- Fear & Greed 20-40 (Medo): LONG so com 4+ confluencias. Reduza confianca em 15 pontos.
- Fear & Greed 60-80 (Ganancia): SHORT tem maior probabilidade. LONG com cautela.
- Fear & Greed >= 80 (Ganancia Extrema): PROIBIDO dar LONG com confianca > 60%. Mercado proximo de topo.
- Leia o campo "data_errors": se houver erros de API, seja conservador e reduza confianca.

PASSO 2 — TENDENCIA (criterios rigorosos):
- UPTREND 4h: preco ACIMA da MM50 E MM50 em direcao ascendente E topos e fundos subindo
- DOWNTREND 4h: preco ABAIXO da MM50 E MM50 em direcao descendente E topos e fundos caindo
- LATERAL: qualquer outro caso — nao classifique como trend sem todos os criterios

PASSO 3 — CONFLUENCIAS (minimo 3 para entrar, 5 para confianca alta):
BOS/CHoCH, order block, FVG, RSI divergencia, volume acima da media, funding favoravel,
suporte/resistencia testado, padrao de vela confirmado

PASSO 4 — DECISAO:
- LONG: macro ok + uptrend confirmado + 3+ confluencias + volume presente
- SHORT: macro ok + downtrend confirmado + 3+ confluencias + volume presente
- WAIT: qualquer duvida, macro adverso, menos de 3 confluencias, volume seco

REGRA DE OURO: confianca deve refletir TECNICA + MACRO + VOLUME juntos.
Confianca alta (>75%) so com tudo alinhado. Se dados de API falharam, cap em 50%.

RESPONDA APENAS COM O JSON, sem texto antes ou depois, sem backticks:
{"signal":"...","confidence":0,"entry_price":0.0,"stop_loss":0.0,"take_profit_1":0.0,"take_profit_2":0.0,"leverage":0,"timeframe":"1h","momento":"...","ganho_maximo":"...","perda_maxima":"...","reasoning_tecnico":"...","reasoning_simples":"...","invalidation":"..."}
"""


class HyperTraderAgent:

    def __init__(self):
        self.ai = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
        self.hl = HyperliquidClient(
            wallet_address=CONFIG["hl_wallet_address"],
            private_key=CONFIG["hl_private_key"],
            testnet=CONFIG["use_testnet"]
        )
        self.analyzer           = MarketAnalyzer(self.hl)
        self.risk               = RiskManager(CONFIG)
        self.history            = []
        self.active_positions   = {}
        self.trade_history      = []
        self.daily_pnl          = 0.0
        self.session_pnl        = 0.0
        self.paused             = False
        self.session_start      = datetime.now(timezone.utc)
        self.telegram_bot       = None
        self.stats              = {"total": 0, "wins": 0, "losses": 0, "best": 0.0, "worst": 0.0}
        self.asset_leverage_map: dict = {}
        self._load_leverage_limits()
        self._4h_cache: dict = {}
        self._4h_cache_time: dict = {}
        self._balance_check_count = 0
        self._last_weak_signal: dict = {}  # cooldown para sinais fracos por simbolo
        logger.info("HyperTrader AI iniciado")
        logger.info(f"Simbolos: {CONFIG['symbols']}")

    def _load_leverage_limits(self):
        try:
            info = self.hl.get_asset_info()
            if not info:
                return
            for sym in CONFIG["symbols"]:
                self.asset_leverage_map[sym] = info.get(sym, {}).get("maxLeverage", CONFIG["leverage_fallback"])
            logger.info("Alavancagens carregadas:")
            for sym, lev in self.asset_leverage_map.items():
                logger.info(f"   {sym}: {lev}x")
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

    def _chat(self, prompt: str, ctx: dict = None) -> str:
        msg = prompt
        if ctx:
            msg += f"\n\n```json\n{json.dumps(ctx, indent=2, default=str)}\n```"
        self.history.append({"role": "user", "content": msg})
        resp = self.ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=TRADER_SYSTEM_PROMPT,
            messages=self.history
        )
        answer = resp.content[0].text
        self.history.append({"role": "assistant", "content": answer})
        if len(self.history) > 40:
            self.history = self.history[-40:]
        return answer

    def _extract_signal(self, text: str) -> Optional[dict]:
        """Extrai o JSON do sinal — tenta varios formatos"""
        # 1. Tenta ```json ... ```
        try:
            for m in re.findall(r"```json\s*(.*?)\s*```", text, re.DOTALL):
                d = json.loads(m)
                if "signal" in d:
                    return d
        except Exception:
            pass
        # 2. Tenta ``` ... ```
        try:
            for m in re.findall(r"```\s*(.*?)\s*```", text, re.DOTALL):
                d = json.loads(m)
                if "signal" in d:
                    return d
        except Exception:
            pass
        # 3. Tenta JSON direto (sem backticks)
        try:
            d = json.loads(text.strip())
            if "signal" in d:
                return d
        except Exception:
            pass
        # 4. Tenta encontrar { ... } no meio do texto
        try:
            m = re.search(r'\{[^{}]*"signal"[^{}]*\}', text, re.DOTALL)
            if m:
                d = json.loads(m.group(0))
                if "signal" in d:
                    return d
        except Exception:
            pass
        # 5. Tenta o maior bloco JSON do texto
        try:
            start = text.find('{')
            end   = text.rfind('}')
            if start != -1 and end != -1:
                d = json.loads(text[start:end+1])
                if "signal" in d:
                    return d
        except Exception:
            pass
        logger.warning(f"Nao foi possivel extrair sinal do texto: {text[:200]}")
        return None

    def analyze_market(self, symbol: str) -> dict:
        logger.info(f"Analisando {symbol}...")
        now = datetime.now(timezone.utc).timestamp()
        cache_age = now - self._4h_cache_time.get(symbol, 0)
        if cache_age > 14400:
            data = self.analyzer.get_full_analysis(symbol)
            self._4h_cache[symbol] = {
                "market_structure": data.get("market_structure", {}),
                "funding": data.get("funding", {}),
                "indicators": {"4h": data.get("indicators", {}).get("4h", {})},
            }
            self._4h_cache_time[symbol] = now
        else:
            data = self.analyzer.get_full_analysis(symbol)
            data["market_structure"] = self._4h_cache.get(symbol, {}).get("market_structure", {})

        platform_max = self.get_symbol_max_leverage(symbol)
        prompt = (
            f"{symbol} perp Hyperliquid. Max leverage: {platform_max}x.\n"
            f"Analise D1+4h+1h. Retorne APENAS o JSON."
        )
        analysis = self._chat(prompt, data)
        signal   = self._extract_signal(analysis)
        if signal:
            logger.info(f"{symbol}: {signal.get('signal')} conf={signal.get('confidence')}%")
        else:
            logger.info(f"{symbol}: sem sinal extraido")
        return {"symbol": symbol, "analysis": analysis, "signal": signal}

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

        risk_pct   = abs(entry - sl) / entry * 100 if entry > 0 else 0
        tp1_pct    = abs(tp1 - entry) / entry * 100 if entry > 0 else 0
        tp2_pct    = abs(tp2 - entry) / entry * 100 if entry > 0 else 0
        risk_usd   = abs(entry - sl) / entry * equity if entry > 0 else 0
        reward_usd = abs(tp1 - entry) / entry * equity if entry > 0 else 0
        rr_ratio   = round(reward_usd / risk_usd, 2) if risk_usd > 0 else 0

        emoji_dir  = "LONG" if direction == "LONG" else "SHORT"
        em         = "🟢" if direction == "LONG" else "🔴"

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
        }

        if CONFIG["dry_run"]:
            self.active_positions[symbol] = pos
            self.stats["total"] += 1
        else:
            is_buy = direction == "LONG"
            try:
                self.hl.set_leverage(symbol, leverage, is_buy)
                self.hl.place_order(symbol=symbol, is_buy=is_buy, size=size,
                                    price=entry, order_type="limit")
                self.hl.place_order(symbol=symbol, is_buy=not is_buy, size=size,
                                    price=sl, order_type="stop", reduce_only=True)
                self.active_positions[symbol] = pos
                self.stats["total"] += 1
            except Exception as e:
                logger.error(f"Erro execucao {symbol}: {e}")
                return {"status": "error", "error": str(e)}

        msg = (
            f"{em} <b>ENTRADA — {emoji_dir}</b>\n"
            f"────────────────────\n"
            f"Par: <code>{symbol}/USDC</code>\n"
            f"Alavancagem: <code>{leverage}x</code> | Confianca: <code>{confidence}%</code>\n\n"
            f"💵 Entrar em: <code>${entry:,.4f}</code>\n"
            f"🛑 Stop Loss: <code>${sl:,.4f}</code> (-{risk_pct:.2f}%)\n"
            f"🎯 1 objetivo: <code>${tp1:,.4f}</code> (+{tp1_pct:.2f}%)\n"
            f"🏆 2 objetivo: <code>${tp2:,.4f}</code> (+{tp2_pct:.2f}%)\n\n"
            f"📐 Risco/Retorno: <code>1:{rr_ratio}</code>\n"
        )
        if ganho:
            msg += f"💰 Ganho maximo: <code>{ganho}</code>\n"
        if perda:
            msg += f"⚠️ Perda maxima: <code>{perda}</code>\n"
        if simple:
            msg += f"\n💬 {simple}\n"
        msg += f"\n🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')} | {'DRY RUN' if CONFIG['dry_run'] else 'LIVE'}"
        self._notify(msg)

        return {"status": "dry_run" if CONFIG["dry_run"] else "executed"}

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
                pos["stop_loss"] = pos["entry"]
                pos["tp1_hit"]   = True
                lev_pnl = pnl_pct * pos["leverage"]
                self._notify(
                    f"🎯 <b>PRIMEIRO OBJETIVO ATINGIDO — {symbol}</b>\n"
                    f"Preco: <code>${current:,.4f}</code>\n"
                    f"Lucro parcial: <code>+{lev_pnl:.2f}%</code>\n"
                    f"50% do lucro realizado!\n"
                    f"Stop movido para entrada — sem risco de perda agora!\n"
                    f"Aguardando 2 objetivo: <code>${pos['tp2']:,.4f}</code>"
                )

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
            self.hl.close_position(symbol, pos["size"], is_long)

        result_em = "✅ LUCRO" if pnl_usd >= 0 else "❌ PERDA"
        em        = "✅" if pnl_usd >= 0 else "❌"

        self._notify(
            f"{em} <b>SAIDA — {result_em}</b>\n"
            f"────────────────────\n"
            f"Par: <code>{symbol}/USDC</code> | {'LONG' if is_long else 'SHORT'}\n"
            f"Motivo: {reason}\n"
            f"Duracao: {dur_min} minutos\n\n"
            f"Entrada: <code>${pos['entry']:,.4f}</code>\n"
            f"Saida: <code>${exit_price:,.4f}</code>\n"
            f"Alavancagem: <code>{pos['leverage']}x</code>\n\n"
            f"<b>Resultado: <code>{pnl_lev:+.2f}%</code> (<code>${pnl_usd:+.2f}</code>)</b>\n"
            f"────────────────────\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
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

    def notify_pnl_update(self):
        if not self.active_positions:
            return
        total = self.stats["total"]
        wr    = round(self.stats["wins"] / total * 100, 1) if total > 0 else 0.0
        lines = [
            f"📊 <b>UPDATE — {datetime.now(timezone.utc).strftime('%H:%M UTC')}</b>",
            "────────────────────",
        ]
        unrealized = 0.0
        for sym, pos in self.active_positions.items():
            cur  = self.hl.get_mark_price(sym)
            il   = pos["direction"] == "LONG"
            pp   = ((cur - pos["entry"]) / pos["entry"] * 100) if il \
                   else ((pos["entry"] - cur) / pos["entry"] * 100)
            pl   = pp * pos["leverage"]
            pu   = (pp / 100) * pos["equity_open"]
            unrealized += pu
            em   = "🟢" if pu >= 0 else "🔴"
            lines.append(
                f"{em} <b>{sym}</b> {'LONG' if il else 'SHORT'} {pos['leverage']}x\n"
                f"  <code>${pos['entry']:,.2f}</code> → <code>${cur:,.2f}</code>\n"
                f"  P&amp;L: <code>{pl:+.2f}%</code> (<code>${pu:+.2f}</code>)"
            )
        lines += [
            "────────────────────",
            f"Nao realizado: <code>${unrealized:+.2f}</code>",
            f"Realizado hoje: <code>${self.session_pnl:+.2f}</code>",
            f"Win Rate: <code>{wr}%</code> ({self.stats['wins']}W/{self.stats['losses']}L)",
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
            "Gere um relatorio executivo da sessao em portugues simples. "
            "O que funcionou, o que nao funcionou, e recomendacoes.", ctx
        )

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

    async def run_autonomous(self):
        logger.info("Loop autonomo iniciado")
        self._notify(
            f"🚀 <b>HyperTrader ATIVO</b>\n"
            f"────────────────────\n"
            f"🌐 {'TESTNET' if CONFIG['use_testnet'] else 'MAINNET'}\n"
            f"⚙️ {'DRY RUN — Simulado' if CONFIG['dry_run'] else 'LIVE — Dinheiro real'}\n"
            f"🎯 Pares: <code>{', '.join(CONFIG['symbols'])}</code>\n"
            f"📊 Max posicoes: <code>{CONFIG['max_positions']}</code>\n"
            f"⏱ Analise a cada: <code>{CONFIG['analysis_interval']}s</code>"
        )

        pnl_tick = 0
        while True:
            try:
                if self.paused:
                    await asyncio.sleep(30)
                    continue

                self._balance_check_count += 1
                if self._balance_check_count % 10 == 0:
                    try:
                        import requests
                        resp = requests.get(
                            "https://api.anthropic.com/v1/organizations/billing/credits",
                            headers={"x-api-key": CONFIG["anthropic_api_key"],
                                     "anthropic-version": "2023-06-01"},
                            timeout=5
                        )
                        if resp.status_code == 200:
                            credits = resp.json().get("available_credits", 9999) / 100
                            if credits < 3:
                                self._notify(
                                    f"⚠️ <b>ALERTA — Creditos Anthropic baixos!</b>\n"
                                    f"Saldo: <code>${credits:.2f}</code>\n"
                                    f"Adicione em: console.anthropic.com"
                                )
                    except Exception:
                        pass

                acc_val = self.hl.get_account_info().get("equity", 10000)
                if self.daily_pnl < 0 and abs(self.daily_pnl) >= acc_val * CONFIG["daily_loss_limit"]:
                    self._notify(
                        f"⛔ <b>STOP DIARIO ATINGIDO</b>\n"
                        f"Perda: <code>${self.daily_pnl:.2f}</code>\nBot pausado por 2h."
                    )
                    await asyncio.sleep(7200)
                    self.daily_pnl = 0.0
                    continue

                self.monitor_positions()

                pnl_tick += 10
                if pnl_tick >= CONFIG["pnl_update_interval"]:
                    self.notify_pnl_update()
                    pnl_tick = 0

                # ── Analisar todas as moedas e notificar CADA resultado ──────────
                resultados = []
                entrou_trade = False

                for sym in CONFIG["symbols"]:
                    if self.paused:
                        break
                    if sym in self.active_positions:
                        # Ja tem posicao aberta — mostra status
                        pos = self.active_positions[sym]
                        cur = self.hl.get_mark_price(sym)
                        il  = pos["direction"] == "LONG"
                        pp  = ((cur - pos["entry"]) / pos["entry"] * 100) if il else ((pos["entry"] - cur) / pos["entry"] * 100)
                        pl  = pp * pos["leverage"]
                        pu  = (pp / 100) * pos["equity_open"]
                        em  = "🟢" if pu >= 0 else "🔴"
                        resultados.append(em + " <b>" + sym + "</b> em posicao aberta | " + f"P&amp;L: <code>{pl:+.2f}%</code>")
                        continue

                    r    = self.analyze_market(sym)
                    s    = r.get("signal")
                    sig  = s.get("signal", "WAIT") if s else "WAIT"
                    conf = s.get("confidence", 0) if s else 0
                    momento = s.get("momento", "") if s else ""

                    if sig == "LONG":
                        em = "🟢"
                        dir_pt = "COMPRAR"
                    elif sig == "SHORT":
                        em = "🔴"
                        dir_pt = "VENDER"
                    else:
                        em = "⚪"
                        dir_pt = "ESPERAR"

                    linha = em + " <b>" + sym + "</b> — " + dir_pt + " <code>" + str(conf) + "%</code>"
                    if momento:
                        linha += "\n    <i>" + str(momento)[:80] + "</i>"
                    resultados.append(linha)

                    if sig in ("LONG", "SHORT") and conf >= CONFIG["min_confidence"]:
                        self.execute_trade(sym, s)
                        entrou_trade = True
                    elif sig in ("LONG", "SHORT") and conf < CONFIG["min_confidence"]:
                        now_ts = datetime.now(timezone.utc).timestamp()
                        last   = self._last_weak_signal.get(sym, 0)
                        if now_ts - last > 3600:
                            self._last_weak_signal[sym] = now_ts

                    await asyncio.sleep(8)

                # ── Resumo do ciclo no Telegram ────────────────────────────────────
                total       = self.stats["total"]
                wr          = round(self.stats["wins"] / total * 100, 1) if total > 0 else 0
                proxima     = datetime.now(timezone.utc).timestamp() + CONFIG["analysis_interval"]
                proxima_str = datetime.fromtimestamp(proxima, tz=timezone.utc).strftime("%H:%M UTC")
                hora_atual  = datetime.now(timezone.utc).strftime("%H:%M UTC")

                resumo = (
                    "🔄 <b>Ciclo de Analise Completo</b>\n"
                    "────────────────────\n"
                    + "\n".join(resultados) + "\n\n"
                    "────────────────────\n"
                    + "📊 Posicoes: <code>" + str(len(self.active_positions)) + "/" + str(CONFIG["max_positions"]) + "</code>\n"
                    + "💵 P&amp;L sessao: <code>$" + f"{self.session_pnl:+.2f}" + "</code>\n"
                    + "🏆 Win Rate: <code>" + str(wr) + "%</code> (" + str(self.stats["wins"]) + "W/" + str(self.stats["losses"]) + "L)\n"
                    + "⏱ Proxima analise: <code>" + proxima_str + "</code>"
                )
                self._notify(resumo)

                logger.info(
                    f"Ciclo completo | "
                    f"Posicoes: {len(self.active_positions)}/{CONFIG['max_positions']} | "
                    f"P&L: ${self.session_pnl:+.2f} | "
                    f"Proxima: {proxima_str}"
                )
                await asyncio.sleep(CONFIG["analysis_interval"])

            except Exception as e:
                logger.error(f"Erro no loop: {e}", exc_info=True)
                await asyncio.sleep(30)


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
                    print(f"\nAgente: {agent._chat(ui)}\n")
            except KeyboardInterrupt:
                break