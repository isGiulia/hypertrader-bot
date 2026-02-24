#!/usr/bin/env python3
"""
🤖 HyperTrader AI — Motor de trading agressivo focado em resultados
Hyperliquid Perpetuals | BTC + Altcoins | Claude AI
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

# ── Logging ──────────────────────────────────────────────────────────────────
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

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═════════════════════════════════════════════════════════════════════════════
CONFIG = {
    # APIs
    "anthropic_api_key":  os.getenv("ANTHROPIC_API_KEY", ""),
    "hl_wallet_address":  os.getenv("HL_WALLET_ADDRESS", ""),
    "hl_private_key":     os.getenv("HL_PRIVATE_KEY", ""),

    # ── Ativos: BTC + Top Altcoins com liquidez na Hyperliquid ───────────
    "symbols": [
        "BTC", "ETH",                          # Blue chips
        "SOL", "AVAX", "BNB",                  # Layer 1s
        "ARB", "OP", "SUI", "APT",             # Layer 2 / novo L1
        "DOGE", "XRP",                         # Grande cap / memecoin
        "LINK", "INJ", "TIA",                  # DeFi / infra
        "WIF", "PEPE",                         # Memecoins high-vol
    ],

    # ── Trading ──────────────────────────────────────────────────────────
    "max_positions":          5,
    "risk_per_trade":         0.025,   # 2.5% do capital por trade
    "default_leverage":       10,      # fallback se API não retornar
    "leverage_fallback":      20,
    "min_confidence":         75,      # confiança mínima — só trades de alta qualidade

    # ── Risco ────────────────────────────────────────────────────────────
    "daily_loss_limit":       0.06,    # 6% = stop diário (em % do capital)
    "take_profit_ratio":      2.0,
    "trailing_stop":          True,
    "partial_tp_pct":         0.50,    # fechar 50% no TP1

    # ── Intervalos ───────────────────────────────────────────────────────
    "analysis_interval":      1800,    # nova rodada a cada 30 min — economiza créditos
    "pnl_update_interval":    120,     # update de P&L no Telegram a cada 2 min

    # ── Modo ─────────────────────────────────────────────────────────────
    "use_testnet":            True,    # ← False para mainnet
    "dry_run":                True,    # ← False para ordens reais

    # ── Telegram ─────────────────────────────────────────────────────────
    "telegram_token":       os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id":     int(os.getenv("TELEGRAM_CHAT_ID", "0")),
    "telegram_allowed_ids": os.getenv("TELEGRAM_ALLOWED_IDS", "0"),
}

# ═════════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT — trader agressivo focado em resultados
# ═════════════════════════════════════════════════════════════════════════════
TRADER_SYSTEM_PROMPT = """Trader profissional de perpetuals na Hyperliquid. Foco em lucro.

REGRAS DE ANÁLISE:
- Analise Diário(tendência) → 4h(estrutura/níveis) → 1h(entrada)
- Entre só com ≥3 confluências técnicas: BOS/CHoCH, order block, FVG, RSI divergência, volume, funding
- RR mínimo 1:2. SL em zona técnica. Alavancagem máxima que o setup suportar
- LONG em bull market / correções em suporte. SHORT em bear market / rejeição em resistência
- WAIT se: lateral sem direção, funding extremo contra, sem confluência

FORMATO DA RESPOSTA — dois campos de texto obrigatórios:
1. "reasoning_tecnico": explicação técnica completa (para traders)
2. "reasoning_simples": explicação em português simples (para qualquer pessoa entender, use analogias do dia a dia, evite jargões)

RESPONDA APENAS COM O JSON (sem texto antes ou depois):
```json
{
  "signal": "LONG ou SHORT ou WAIT",
  "confidence": 0,
  "entry_price": 0.0,
  "stop_loss": 0.0,
  "take_profit_1": 0.0,
  "take_profit_2": 0.0,
  "leverage": 0,
  "timeframe": "1h",
  "reasoning_tecnico": "análise técnica detalhada em 2-3 frases",
  "reasoning_simples": "explicação simples com analogia do dia a dia em 2-3 frases",
  "invalidation": "o que cancela o setup em linguagem simples"
}
```
"""


# ═════════════════════════════════════════════════════════════════════════════
#  AGENTE PRINCIPAL
# ═════════════════════════════════════════════════════════════════════════════
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
        self.stats              = {"total": 0, "wins": 0, "losses": 0,
                                   "best": 0.0, "worst": 0.0}
        # Carregar alavancagem máxima real de cada ativo via API
        self.asset_leverage_map: dict = {}
        self._load_leverage_limits()
        # Cache de análise 4h — só recalcula quando timeframe muda
        self._4h_cache: dict = {}
        self._4h_cache_time: dict = {}
        logger.info("🤖 HyperTrader AI iniciado")
        logger.info(f"Símbolos: {CONFIG['symbols']}")

    # ── Leverage dinâmico via API ─────────────────────────────────────────────

    def _load_leverage_limits(self):
        """Busca o maxLeverage real de cada ativo direto da API da Hyperliquid."""
        try:
            info = self.hl.get_asset_info()
            if not info:
                logger.warning("⚠️  API retornou vazio — usando fallback de alavancagem")
                return
            for sym in CONFIG["symbols"]:
                asset = info.get(sym, {})
                max_lev = asset.get("maxLeverage", CONFIG["leverage_fallback"])
                self.asset_leverage_map[sym] = max_lev
            logger.info("🔢 Alavancagens máximas carregadas da Hyperliquid:")
            for sym, lev in self.asset_leverage_map.items():
                logger.info(f"   {sym}: {lev}x")
        except Exception as e:
            logger.error(f"Erro ao carregar leverage limits: {e}")

    def get_symbol_max_leverage(self, symbol: str) -> int:
        """Retorna o maxLeverage real do símbolo (carregado da API ou buscado on demand)."""
        if symbol in self.asset_leverage_map:
            return self.asset_leverage_map[symbol]
        # Tentar buscar sob demanda
        try:
            lev = self.hl.get_max_leverage(symbol)
            self.asset_leverage_map[symbol] = lev
            return lev
        except Exception:
            return CONFIG["leverage_fallback"]

    # ── LLM ──────────────────────────────────────────────────────────────────

    def _chat(self, prompt: str, ctx: dict = None) -> str:
        msg = prompt
        if ctx:
            msg += f"\n\n```json\n{json.dumps(ctx, indent=2, default=str)}\n```"
        self.history.append({"role": "user", "content": msg})
        resp = self.ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,  # haiku é rápido e barato
            system=TRADER_SYSTEM_PROMPT,
            messages=self.history
        )
        answer = resp.content[0].text
        self.history.append({"role": "assistant", "content": answer})
        if len(self.history) > 40:
            self.history = self.history[-40:]
        return answer

    def _extract_signal(self, text: str) -> Optional[dict]:
        try:
            for m in re.findall(r"```json\s*(.*?)\s*```", text, re.DOTALL):
                d = json.loads(m)
                if "signal" in d:
                    return d
        except Exception:
            pass
        return None

    # ── Análise ───────────────────────────────────────────────────────────────

    def analyze_market(self, symbol: str) -> dict:
        logger.info(f"🔍 {symbol}...")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).timestamp()
        # Cache 4h — só busca dados completos a cada 4 horas por símbolo
        cache_age = now - self._4h_cache_time.get(symbol, 0)
        if cache_age > 14400:  # 4 horas
            data = self.analyzer.get_full_analysis(symbol)
            # Guardar só o essencial no cache (economiza memória)
            self._4h_cache[symbol] = {
                "market_structure": data.get("market_structure", {}),
                "funding": data.get("funding", {}),
                "indicators": {"4h": data.get("indicators", {}).get("4h", {})},
            }
            self._4h_cache_time[symbol] = now
        else:
            # Busca apenas dados de curto prazo (1h/15m) — muito mais barato
            data = self.analyzer.get_full_analysis(symbol)
            # Injeta cache do 4h
            data["market_structure"] = self._4h_cache.get(symbol, {}).get("market_structure", {})
        platform_max = self.get_symbol_max_leverage(symbol)
        prompt = (
            f"{symbol} perp Hyperliquid. Max leverage: {platform_max}x.\n"
            f"Analise D1(tendência)+4h(estrutura)+1h(entrada). "
            f"Dados abaixo. Retorne APENAS o JSON."
        )
        analysis = self._chat(prompt, data)
        signal   = self._extract_signal(analysis)
        return {"symbol": symbol, "analysis": analysis, "signal": signal}

    # ── Execução ──────────────────────────────────────────────────────────────

    def execute_trade(self, symbol: str, signal: dict) -> dict:
        if not signal or signal.get("signal") == "WAIT":
            return {"status": "skipped"}

        direction  = signal["signal"]
        entry      = float(signal.get("entry_price", 0))
        sl         = float(signal.get("stop_loss", 0))
        tp1        = float(signal.get("take_profit_1", 0))
        tp2        = float(signal.get("take_profit_2", 0))
        confidence = signal.get("confidence", 0)
        # Usar o máximo real permitido pela Hyperliquid para este símbolo
        platform_max = self.get_symbol_max_leverage(symbol)
        # Respeitar o que a IA sugeriu, mas jamais exceder o limite da plataforma
        suggested    = int(signal.get("leverage", platform_max))
        leverage     = min(suggested, platform_max)
        reasoning  = signal.get("reasoning", "")

        val = self.risk.validate_trade(signal, self.active_positions, self.daily_pnl)
        if not val["approved"]:
            logger.warning(f"Bloqueado [{symbol}]: {val['reason']}")
            return {"status": "blocked", "reason": val["reason"]}

        acc    = self.hl.get_account_info()
        equity = acc.get("equity", 1000)
        size   = self.risk.calculate_position_size(equity, entry, sl)

        risk_usd   = abs(entry - sl) / entry * equity if entry > 0 else 0
        reward_usd = abs(tp1 - entry) / entry * equity if entry > 0 else 0
        rr_ratio   = round(reward_usd / risk_usd, 2) if risk_usd > 0 else 0
        risk_pct   = abs(entry - sl) / entry * 100 if entry > 0 else 0
        tp1_pct    = abs(tp1 - entry) / entry * 100 if entry > 0 else 0
        tp2_pct    = abs(tp2 - entry) / entry * 100 if entry > 0 else 0

        emoji_dir  = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
        mode_label = "🔵 DRY RUN" if CONFIG["dry_run"] else "🔴 LIVE"

        # Registra posição
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
                order = self.hl.place_order(symbol=symbol, is_buy=is_buy, size=size,
                                            price=entry, order_type="limit")
                self.hl.place_order(symbol=symbol, is_buy=not is_buy, size=size,
                                    price=sl, order_type="stop", reduce_only=True)
                self.active_positions[symbol] = pos
                self.stats["total"] += 1
                logger.info(f"✅ {direction} {symbol} @ ${entry:,.4f}")
            except Exception as e:
                logger.error(f"Erro execução {symbol}: {e}")
                return {"status": "error", "error": str(e)}

        # Notificação de entrada
        self._notify(
            f"⚡ *ENTRADA — {emoji_dir}*\n"
            f"{'─'*32}\n"
            f"📌 *Par:* `{symbol}/USDC`\n"
            f"🔢 *Alavancagem:* `{leverage}x`\n"
            f"🤖 *Confiança IA:* `{confidence}%`\n"
            f"\n"
            f"💵 *Entrada:* `${entry:,.4f}`\n"
            f"🛑 *Stop Loss:* `${sl:,.4f}` `(-{risk_pct:.2f}%)`\n"
            f"🎯 *TP1:* `${tp1:,.4f}` `(+{tp1_pct:.2f}%)`\n"
            f"🏆 *TP2:* `${tp2:,.4f}` `(+{tp2_pct:.2f}%)`\n"
            f"\n"
            f"📐 *R:R:* `1:{rr_ratio}`\n"
            f"📊 *Tamanho:* `{size:.4f} {symbol}`\n"
            f"💰 *Capital:* `${equity:,.2f}`\n"
            f"\n"
            f"💡 _{reasoning}_\n"
            f"{'─'*32}\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} | {mode_label}"
        )

        return {"status": "dry_run" if CONFIG["dry_run"] else "executed"}

    # ── Monitor posições ──────────────────────────────────────────────────────

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
                self._close_position(symbol, pos, current, "🛑 Stop Loss")
            elif hit_tp2:
                self._close_position(symbol, pos, current, "🏆 TP2 — Full Exit")
            elif hit_tp1:
                # Parcial: fechar 50% + mover SL para BE
                partial = pos["size"] * CONFIG["partial_tp_pct"]
                if not CONFIG["dry_run"]:
                    self.hl.place_order(symbol=symbol, is_buy=not is_long,
                                        size=partial, price=current,
                                        order_type="market", reduce_only=True)
                pos["size"]     -= partial
                pos["stop_loss"] = pos["entry"]   # break even
                pos["tp1_hit"]   = True
                lev_pnl          = pnl_pct * pos["leverage"]
                self._notify(
                    f"🎯 *TP1 ATINGIDO — {symbol}*\n"
                    f"Preço: `${current:,.4f}` | P&L: `+{lev_pnl:.2f}%`\n"
                    f"✅ 50% fechado com lucro\n"
                    f"🔒 Stop movido para Break Even\n"
                    f"🎯 Restante aguardando TP2: `${pos['tp2']:,.4f}`"
                )

    def _close_position(self, symbol: str, pos: dict, exit_price: float, reason: str):
        is_long  = pos["direction"] == "LONG"
        pnl_pct  = ((exit_price - pos["entry"]) / pos["entry"] * 100) if is_long \
                   else ((pos["entry"] - exit_price) / pos["entry"] * 100)
        pnl_lev  = pnl_pct * pos["leverage"]
        pnl_usd  = (pnl_pct / 100) * pos["equity_open"]
        risk_pct = abs(pos["entry"] - pos["stop_loss"]) / pos["entry"] * 100
        dur_min  = round((datetime.now(timezone.utc) -
                          datetime.fromisoformat(pos["opened_at"].replace("Z","")
                          .replace("+00:00",""))).total_seconds() / 60)

        if not CONFIG["dry_run"]:
            self.hl.close_position(symbol, pos["size"], is_long)

        result_em = "✅ LUCRO" if pnl_usd >= 0 else "❌ PERDA"
        dir_label = "🟢 LONG" if is_long else "🔴 SHORT"

        self._notify(
            f"🏁 *SAÍDA — {result_em}*\n"
            f"{'─'*32}\n"
            f"📌 *Par:* `{symbol}/USDC` | {dir_label}\n"
            f"📋 *Motivo:* {reason}\n"
            f"⏱ *Duração:* `{dur_min} min`\n"
            f"\n"
            f"📥 *Entrada:* `${pos['entry']:,.4f}`\n"
            f"📤 *Saída:* `${exit_price:,.4f}`\n"
            f"🔢 *Alavancagem:* `{pos['leverage']}x`\n"
            f"\n"
            f"💰 *P&L:* `{pnl_lev:+.2f}%` (`${pnl_usd:+.2f}`)\n"
            f"📊 *P&L sem lev.:* `{pnl_pct:+.2f}%`\n"
            f"🛑 *SL estava em:* `{risk_pct:.2f}%`\n"
            f"{'─'*32}\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        )

        # Estatísticas
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

    # ── Update periódico de P&L ───────────────────────────────────────────────

    def notify_pnl_update(self):
        total = self.stats["total"]
        wr    = round(self.stats["wins"] / total * 100, 1) if total > 0 else 0.0

        lines = [
            f"📊 *UPDATE P&L — {datetime.now(timezone.utc).strftime('%H:%M UTC')}*",
            f"{'─'*32}",
        ]

        unrealized = 0.0
        if self.active_positions:
            lines.append("📈 *Posições abertas:*")
            for sym, pos in self.active_positions.items():
                cur    = self.hl.get_mark_price(sym)
                il     = pos["direction"] == "LONG"
                pp     = ((cur - pos["entry"]) / pos["entry"] * 100) if il \
                         else ((pos["entry"] - cur) / pos["entry"] * 100)
                pl     = pp * pos["leverage"]
                pu     = (pp / 100) * pos["equity_open"]
                unrealized += pu
                em     = "🟢" if pu >= 0 else "🔴"
                dl     = "L" if il else "S"
                lines.append(
                    f"  {em} *{sym}* [{dl}{pos['leverage']}x] `${cur:,.4f}`\n"
                    f"     P&L: `{pl:+.2f}%` (`${pu:+.2f}`)"
                )
        else:
            lines.append("📭 Nenhuma posição aberta")

        lines += [
            f"{'─'*32}",
            f"📈 *Não realizado:* `${unrealized:+.2f}`",
            f"💵 *Realizado sessão:* `${self.session_pnl:+.2f}`",
            f"💰 *P&L Total:* `${(self.session_pnl + unrealized):+.2f}`",
            f"{'─'*16}",
            f"🏆 *Win Rate:* `{wr}%` ({self.stats['wins']}W / {self.stats['losses']}L)",
            f"📋 *Trades:* `{total}` | Abertas: `{len(self.active_positions)}/{CONFIG['max_positions']}`",
            f"🏅 *Melhor:* `${self.stats['best']:+.2f}` | Pior: `${self.stats['worst']:+.2f}`",
        ]
        self._notify("\n".join(lines))

    # ── Relatório ─────────────────────────────────────────────────────────────

    def generate_daily_report(self) -> str:
        total = self.stats["total"]
        wr    = round(self.stats["wins"] / total * 100, 1) if total > 0 else 0
        ctx   = {
            "session_start":  self.session_start.isoformat(),
            "session_pnl":    self.session_pnl,
            "open_positions": len(self.active_positions),
            "total_trades":   total,
            "win_rate":       f"{wr}%",
            "wins":           self.stats["wins"],
            "losses":         self.stats["losses"],
            "best_trade":     self.stats["best"],
            "worst_trade":    self.stats["worst"],
        }
        return self._chat(
            "Gere um relatório executivo da sessão. O que funcionou, o que não funcionou, "
            "e recomendações para próxima sessão.", ctx
        )

    # ── Notificação ───────────────────────────────────────────────────────────

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
                logger.error(f"Telegram notify error: {e}")

    # ═════════════════════════════════════════════════════════════════════════
    #  LOOP AUTÔNOMO
    # ═════════════════════════════════════════════════════════════════════════

    async def run_autonomous(self):
        logger.info("🚀 Loop autônomo iniciado")
        self._notify(
            f"🚀 *HyperTrader ATIVO*\n{'─'*30}\n"
            f"🌐 `{'TESTNET' if CONFIG['use_testnet'] else 'MAINNET'}`\n"
            f"⚙️ `{'DRY RUN' if CONFIG['dry_run'] else '🔴 LIVE TRADING'}`\n"
            f"🎯 Pares: `{', '.join(CONFIG['symbols'])}`\n"
            f"📊 Max posições: `{CONFIG['max_positions']}`\n"
            f"⏱ Análise a cada: `{CONFIG['analysis_interval']}s`"
        )

        pnl_tick = 0

        while True:
            try:
                if self.paused:
                    await asyncio.sleep(30)
                    continue

                # Verificar saldo Anthropic a cada 10 ciclos
                if not hasattr(self, "_balance_check_count"):
                    self._balance_check_count = 0
                self._balance_check_count += 1
                if self._balance_check_count % 10 == 0:
                    try:
                        import requests
                        headers = {
                            "x-api-key": CONFIG["anthropic_api_key"],
                            "anthropic-version": "2023-06-01"
                        }
                        resp = requests.get(
                            "https://api.anthropic.com/v1/organizations/billing/credits",
                            headers=headers, timeout=5
                        )
                        if resp.status_code == 200:
                            credits = resp.json().get("available_credits", 9999) / 100
                            if credits < 3:
                                self._notify(
                                    f"⚠️ <b>ALERTA — Créditos Anthropic baixos!</b>\n"
                                    f"Saldo restante: <code>${credits:.2f}</code>\n"
                                    f"Adicione créditos em: console.anthropic.com/settings/billing"
                                )
                    except Exception:
                        pass

                # Stop diário por valor absoluto estimado
                acc_val = self.hl.get_account_info().get("equity", 10000)
                daily_limit_usd = acc_val * CONFIG["daily_loss_limit"]
                if self.daily_pnl < 0 and abs(self.daily_pnl) >= daily_limit_usd:
                    self._notify(
                        f"⛔ *STOP DIÁRIO ATINGIDO*\n"
                        f"Perda: `${self.daily_pnl:.2f}`\nBot pausado por 2h."
                    )
                    await asyncio.sleep(7200)
                    self.daily_pnl = 0.0
                    continue

                # Monitorar posições abertas
                self.monitor_positions()

                # Update periódico de P&L
                pnl_tick += 10
                if pnl_tick >= CONFIG["pnl_update_interval"]:
                    self.notify_pnl_update()
                    pnl_tick = 0

                # Buscar novos setups
                if len(self.active_positions) < CONFIG["max_positions"]:
                    for sym in CONFIG["symbols"]:
                        if sym in self.active_positions or self.paused:
                            continue
                        r = self.analyze_market(sym)
                        s = r.get("signal")
                        if s and s.get("signal") in ("LONG", "SHORT"):
                            conf = s.get("confidence", 0)
                            if conf >= CONFIG["min_confidence"]:
                                self.execute_trade(sym, s)
                            else:
                                logger.info(f"⏭ {sym}: conf={conf}% < {CONFIG['min_confidence']}%")
                        else:
                            logger.info(f"⏭ {sym}: WAIT")
                        await asyncio.sleep(8)

                logger.info(
                    f"💤 {CONFIG['analysis_interval']}s | "
                    f"Posições: {len(self.active_positions)}/{CONFIG['max_positions']} | "
                    f"P&L sessão: ${self.session_pnl:+.2f}"
                )
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
                logger.info("📱 Telegram Bot ativo")

                # Inicializar bot
                await tg.app.initialize()
                await tg.app.start()
                await tg.app.updater.start_polling(drop_pending_updates=True)

                # Rodar trading loop e bot juntos no mesmo event loop
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
        print("\n🤖 HyperTrader — Modo Interativo")
        print("Comandos: analisar BTC | posicoes | relatorio | sair\n")
        while True:
            try:
                ui = input("Você: ").strip()
                if not ui:
                    continue
                if ui.lower() in ("sair", "exit"):
                    break
                elif ui.lower().startswith("analisar "):
                    sym = ui.split()[-1].upper()
                    r   = agent.analyze_market(sym)
                    print(f"\n{r['analysis']}\n")
                    if r["signal"] and r["signal"].get("signal") != "WAIT":
                        agent.execute_trade(sym, r["signal"])
                elif ui.lower() == "posicoes":
                    print(json.dumps(agent.active_positions, indent=2, default=str))
                elif ui.lower() == "relatorio":
                    print(agent.generate_daily_report())
                else:
                    print(f"\nAgente: {agent._chat(ui)}\n")
            except KeyboardInterrupt:
                break
