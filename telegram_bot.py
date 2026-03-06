"""
HyperTrader Telegram Bot
Interface completa: comandos, botoes, notificacoes de trade e P&L em tempo real
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
try:
    _DISPLAY_TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    _DISPLAY_TZ = timezone.utc

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

from hypertrader import config

logger = logging.getLogger("TelegramBot")

_allowed_raw = (os.getenv("TELEGRAM_ALLOWED_IDS") or "").strip()
# Se não configurar, libera apenas leitura via Telegram (sem bloqueio).
# Para segurança, configure TELEGRAM_ALLOWED_IDS="123,456".
if not _allowed_raw or _allowed_raw == "0":
    ALLOWED_IDS = []
else:
    ALLOWED_IDS = [
        int(x.strip()) for x in _allowed_raw.split(",")
        if x.strip().isdigit()
    ]


def auth(func):
    import functools
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        update = next((a for a in args if hasattr(a, "effective_user")), None)
        if not update or not update.effective_user:
            return
        uid = update.effective_user.id
        if ALLOWED_IDS and uid not in ALLOWED_IDS:
            if update.message:
                await update.message.reply_text("Acesso nao autorizado.")
            return
        return await func(*args, **kwargs)
    return wrapper


class TelegramBot:

    def __init__(self, agent, token: str):
        self.agent = agent
        self.token = token
        self.app   = Application.builder().token(token).build()
        self._register()
        logger.info("TelegramBot inicializado")

    def _register(self):
        a = self.app
        a.add_handler(CommandHandler("start",       self.cmd_start))
        a.add_handler(CommandHandler("status",      self.cmd_status))
        a.add_handler(CommandHandler("posicoes",    self.cmd_posicoes))
        a.add_handler(CommandHandler("pnl",         self.cmd_pnl))
        a.add_handler(CommandHandler("analisar",    self.cmd_analisar))
        a.add_handler(CommandHandler("historico",   self.cmd_historico))
        a.add_handler(CommandHandler("pausar",      self.cmd_pausar))
        a.add_handler(CommandHandler("retomar",     self.cmd_retomar))
        a.add_handler(CommandHandler("fechar",      self.cmd_fechar))
        a.add_handler(CommandHandler("ajuda",       self.cmd_ajuda))
        a.add_handler(CallbackQueryHandler(self.handle_callback))
        # Sem "chat livre" na V1 — reduz risco de comando ambíguo.

    def _safe(self, text: str) -> str:
        return str(text).replace("<", "").replace(">", "").replace("&", "e")

    def _format_signal(self, symbol: str, signal: dict) -> str:
        sig     = signal.get("signal", "WAIT")
        conf    = signal.get("confidence", 0)
        entry   = signal.get("entry_price", 0)
        sl      = signal.get("stop_loss", 0)
        tp1     = signal.get("take_profit_1", 0)
        tp2     = signal.get("take_profit_2", 0)
        lev     = signal.get("leverage", 0)
        momento = signal.get("momento", "")
        simple  = signal.get("reasoning_simples", signal.get("reasoning", ""))
        tec     = signal.get("reasoning_tecnico", "")
        inv     = signal.get("invalidation", "")
        ganho   = signal.get("ganho_maximo", "")
        perda   = signal.get("perda_maxima", "")

        if sig == "LONG":    decisao = "🟢 HORA DE COMPRAR"
        elif sig == "SHORT": decisao = "🔴 HORA DE VENDER"
        else:                decisao = "⏳ AGUARDAR"

        msg = (
            f"📊 <b>Analise {symbol}</b>\n"
            f"════════════════════\n"
            f"<b>{decisao}</b>\n"
            f"Confianca: <b>{conf}%</b>\n\n"
        )
        if momento: msg += f"💡 <b>Resumo:</b>\n{self._safe(momento)}\n\n"
        if simple:  msg += f"💬 <b>Em palavras simples:</b>\n{self._safe(simple)}\n\n"

        if sig in ("LONG", "SHORT") and entry > 0:
            msg += (
                f"════════════════════\n"
                f"💵 <b>Entrar em:</b> <code>${entry:,.4f}</code>\n"
                f"🛑 <b>Stop Loss:</b> <code>${sl:,.4f}</code>\n"
                f"🎯 <b>TP1:</b> <code>${tp1:,.4f}</code>\n"
                f"🏆 <b>TP2:</b> <code>${tp2:,.4f}</code>\n"
                f"🔢 <b>Alavancagem:</b> <code>{lev}x</code>\n\n"
            )
            if ganho: msg += f"💰 <b>Ganho maximo:</b> <code>{self._safe(ganho)}</code>\n"
            if perda: msg += f"⚠️ <b>Perda maxima:</b> <code>{self._safe(perda)}</code>\n"
            if inv:   msg += f"❌ <b>Cancela se:</b> {self._safe(inv)}\n"
        elif sig == "WAIT":
            if inv: msg += f"✅ <b>Vou entrar quando:</b>\n{self._safe(inv)}\n"

        if tec: msg += f"\n📈 <b>Tecnico:</b>\n<i>{self._safe(tec[:400])}</i>\n"
        msg += f"\n🕐 {datetime.now(_DISPLAY_TZ).strftime('%H:%M %Z')}"
        return msg

    # ── Comandos

    @auth
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        
        syms = config.SYMBOLS
        # Gera botoes de analise dinamicamente com os simbolos reais do CONFIG
        analyze_btns = [
            InlineKeyboardButton(f"🔍 {s}", callback_data=f"analisar_{s}")
            for s in syms
        ]
        # Agrupa em pares
        analyze_rows = [analyze_btns[i:i+2] for i in range(0, len(analyze_btns), 2)]

        kb = [
            [InlineKeyboardButton("📊 Status",    callback_data="status"),
             InlineKeyboardButton("💰 P&L",        callback_data="pnl")],
            [InlineKeyboardButton("📈 Posicoes",   callback_data="posicoes"),
             InlineKeyboardButton("📋 Historico",  callback_data="historico")],
            *analyze_rows,
            [InlineKeyboardButton("📖 Ajuda",      callback_data="ajuda")],
            [InlineKeyboardButton("⏸ Pausar",     callback_data="pausar"),
             InlineKeyboardButton("▶️ Retomar",    callback_data="retomar")],
        ]
        modo = "LIVE 🔴" if not config.DRY_RUN else "DRY RUN 🔵"
        rede = "MAINNET" if not config.USE_TESTNET else "TESTNET"
        await update.message.reply_text(
            f"🤖 <b>HyperTrader AI</b>\n"
            f"────────────────────\n"
            f"Rede: <code>{rede}</code> | Modo: <code>{modo}</code>\n"
            f"Pares: <code>{', '.join(syms)}</code>\n"
            f"\nUse os botões (ou /ajuda).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    @auth
    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("⏳ Buscando...")
        await m.edit_text(await self._status_text(), parse_mode="HTML")

    @auth
    async def cmd_pnl(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("⏳ Calculando P&L...")
        await m.edit_text(await self._pnl_text(), parse_mode="HTML")

    @auth
    async def cmd_posicoes(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("⏳ Carregando...")
        text = await asyncio.get_event_loop().run_in_executor(None, self._positions_text)
        await m.edit_text(text, parse_mode="HTML")

    @auth
    async def cmd_analisar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        symbol = ctx.args[0].upper() if ctx.args else "BTC"
        await self._do_analyze(update, symbol)

    @auth
    async def cmd_historico(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._history_text(), parse_mode="HTML")

    @auth
    async def cmd_pausar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        self.agent.paused = True
        await update.message.reply_text("⏸ <b>Bot pausado.</b>", parse_mode="HTML")

    @auth
    async def cmd_retomar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        self.agent.paused = False
        await update.message.reply_text("▶️ <b>Bot retomado!</b>", parse_mode="HTML")

    @auth
    async def cmd_fechar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not ctx.args:
            await update.message.reply_text("Uso: /fechar BTC")
            return
        sym = ctx.args[0].upper()
        if sym not in self.agent.active_positions:
            await update.message.reply_text(f"Sem posicao aberta em {sym}.")
            return
        r = await asyncio.get_event_loop().run_in_executor(
            None, self.agent.close_position_manual, sym, "Manual via Telegram"
        )
        if r.get("status") == "closed":
            await update.message.reply_text(f"✅ Fechada: <b>{sym}</b>.", parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ Não consegui fechar: {r}", parse_mode="HTML")

    @auth
    async def cmd_ajuda(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📖 <b>Comandos disponiveis:</b>\n\n"
            "/start — Menu principal com botoes\n"
            "/status — Status do bot e conta\n"
            "/pnl — Lucro e perda em tempo real\n"
            "/posicoes — Posicoes abertas agora\n"
            "/historico — Historico de trades\n"
            "/analisar BTC — Analisar qualquer moeda\n"
            "/pausar — Parar novas analises\n"
            "/retomar — Retomar o bot\n"
            "/fechar BTC — Fechar posicao manualmente\n"
            "\n🧠 <b>V1:</b> sem chat livre (evita comandos ambiguos e spam).",
            parse_mode="HTML"
        )

    # ── Callbacks

    @auth
    async def handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        d = q.data
        try:
            if d == "status":
                await q.edit_message_text(await self._status_text(), parse_mode="HTML")
            elif d == "pnl":
                await q.edit_message_text(await self._pnl_text(), parse_mode="HTML")
            elif d == "posicoes":
                await q.edit_message_text(self._positions_text(), parse_mode="HTML")
            elif d == "historico":
                await q.edit_message_text(self._history_text(), parse_mode="HTML")
            elif d.startswith("analisar_"):
                sym = d.split("_", 1)[1]
                await q.edit_message_text(f"🔍 Analisando <b>{sym}</b>...", parse_mode="HTML")
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self.agent.analyze_market, sym)
                signal = result.get("signal")
                text   = self._format_signal(sym, signal) if signal else f"Sem dados para {sym}."
                await q.edit_message_text(text, parse_mode="HTML")
            elif d == "pausar":
                self.agent.paused = True
                await q.edit_message_text("⏸ <b>Bot pausado.</b>", parse_mode="HTML")
            elif d == "retomar":
                self.agent.paused = False
                await q.edit_message_text("▶️ <b>Bot retomado!</b>", parse_mode="HTML")
            elif d == "ajuda":
                await q.edit_message_text(
                    "📖 <b>Comandos:</b>\n"
                    "/status /pnl /posicoes /historico\n"
                    "/analisar BTC /pausar /retomar\n"
                    "/fechar BTC",
                    parse_mode="HTML")
        except Exception as e:
            logger.error(f"Callback error [{d}]: {e}")
            try:
                await q.edit_message_text(f"Erro: {e}")
            except Exception:
                pass

    @auth
    async def handle_msg(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        m    = await update.message.reply_text("🤔 Pensando...")
        try:
            resp = await asyncio.get_event_loop().run_in_executor(
                None, self.agent._chat, text)
            if len(resp) > 4000:
                resp = resp[:4000] + "\n...[truncado]"
            await m.edit_text(self._safe(resp))
        except Exception as e:
            await m.edit_text(f"Erro: {e}")

    # ── Textos de status/pnl

    async def _status_text(self) -> str:
        try:
            acc    = await asyncio.get_event_loop().run_in_executor(
                None, self.agent.hl.get_account_info)
            equity = acc.get("equity", 0)
            margin = acc.get("margin_used", 0)
            free   = acc.get("free_margin", max(equity - margin, 0))
            
            total  = self.agent.stats["total"]
            wr     = round(self.agent.stats["wins"] / total * 100, 1) if total > 0 else 0
            dd_pct = self.agent._risk.get_drawdown_pct(equity)

            pause_extra = ""
            try:
                if self.agent._state.pause_until_ts and self.agent._state.pause_until_ts > 0:
                    until = datetime.fromtimestamp(self.agent._state.pause_until_ts, tz=_DISPLAY_TZ).strftime('%Y-%m-%d %H:%M %Z')
                    pause_extra = f"\n⏳ <b>Pausa automática:</b> <code>{until}</code>\n🧾 Motivo: <code>{self._safe(self.agent._state.pause_reason)}</code>"
            except Exception:
                pass
            return (
                f"🤖 <b>HyperTrader — Status</b>\n"
                f"────────────────────\n"
                f"{'⏸ PAUSADO' if (self.agent.paused or (self.agent._state.pause_until_ts and self.agent._state.pause_until_ts > 0)) else '✅ RODANDO'}{pause_extra}\n"
                f"🌐 <code>{'TESTNET' if config.USE_TESTNET else 'MAINNET'}</code> | "
                f"<code>{'DRY RUN' if config.DRY_RUN else 'LIVE 🔴'}</code>\n\n"
                f"💰 <b>Equity:</b> <code>${equity:,.2f}</code>\n"
                f"📊 <b>Margin:</b> <code>${margin:,.2f}</code>\n"
                f"🆓 <b>Livre:</b> <code>${free:,.2f}</code>\n"
                f"📉 <b>Drawdown:</b> <code>{dd_pct:.1f}%</code>\n\n"
                f"📈 <b>Posicoes:</b> <code>{len(self.agent.active_positions)}/{config.MAX_POSITIONS}</code>\n"
                f"🔢 <b>Trades hoje:</b> <code>{self.agent._state.daily_trades_count}/{config.MAX_TRADES_DAY}</code>\n"
                f"💵 <b>P&amp;L hoje:</b> <code>${self.agent.daily_pnl:+.2f}</code>\n"
                f"💵 <b>P&amp;L sessao:</b> <code>${self.agent.session_pnl:+.2f}</code>\n"
                f"🏆 <b>Win Rate:</b> <code>{wr}%</code> ({self.agent.stats['wins']}W/{self.agent.stats['losses']}L)\n"
                f"📋 <b>Trades total:</b> <code>{total}</code>\n\n"
                f"🕐 {datetime.now(_DISPLAY_TZ).strftime('%Y-%m-%d %H:%M %Z')}"
            )
        except Exception as e:
            return f"Erro ao buscar status: {e}"

    async def _pnl_text(self) -> str:
        total = self.agent.stats["total"]
        wr    = round(self.agent.stats["wins"] / total * 100, 1) if total > 0 else 0
        unr   = 0.0
        real_positions = {}
        if self.agent.active_positions and (not config.DRY_RUN):
            try:
                real_positions = await asyncio.get_event_loop().run_in_executor(
                    None, self.agent.hl.get_all_positions
                )
            except Exception:
                real_positions = {}
        lines = [
            f"💰 <b>P&amp;L — {datetime.now(_DISPLAY_TZ).strftime('%H:%M %Z')}</b>",
            "────────────────────"
        ]
        if self.agent.active_positions:
            lines.append("📈 <b>Posicoes Abertas:</b>")
            for sym, pos in self.agent.active_positions.items():
                cur  = self.agent.hl.get_mark_price(sym)
                il   = pos["direction"] == "LONG"
                entry = float(pos.get("entry", 0) or cur)
                pp   = ((cur - entry) / entry * 100) if il else ((entry - cur) / entry * 100)
                pl   = pp * float(pos.get("leverage", 1) or 1)

                # Preferir PnL real da exchange
                pu = None
                if sym in real_positions:
                    pu = float(real_positions[sym].get("unrealized_pnl", 0) or 0)
                else:
                    # fallback aproximado
                    notional = entry * float(pos.get("size", 0) or 0)
                    pu = (pp / 100.0) * notional

                unr  += float(pu)
                em   = "🟢" if pu >= 0 else "🔴"
                st   = "⏳" if pos.get("status") == "pending_fill" else ""
                notional = cur * float(pos.get("size", 0) or 0)
                margin = notional / float(pos.get("leverage", 1) or 1) if notional > 0 else 0
                lines.append(
                    f"\n{em}{st} <b>{sym}</b> | {'LONG' if il else 'SHORT'} {pos['leverage']}x\n"
                    f"  Entrada: <code>${entry:,.4f}</code> → <code>${cur:,.4f}</code>\n"
                    f"  Alocado: <code>${margin:,.2f}</code> (margem) | Notional: <code>${notional:,.2f}</code>\n"
                    f"  P&amp;L: <code>{pl:+.2f}%</code> (<code>${pu:+.2f}</code>)"
                )
        else:
            lines.append("📭 Nenhuma posicao aberta")
        lines += [
            "\n────────────────────",
            f"📈 <b>Nao realizado:</b> <code>${unr:+.2f}</code>",
            f"💵 <b>Realizado hoje:</b> <code>${self.agent.daily_pnl:+.2f}</code>",
            f"💰 <b>Total:</b> <code>${(self.agent.daily_pnl + unr):+.2f}</code>",
            "────────────────────",
            f"🏆 <b>Win Rate:</b> <code>{wr}%</code> ({self.agent.stats['wins']}W/{self.agent.stats['losses']}L)",
            f"🏅 <b>Melhor:</b> <code>${self.agent.stats['best']:+.2f}</code>",
            f"😢 <b>Pior:</b> <code>${self.agent.stats['worst']:+.2f}</code>",
        ]
        return "\n".join(lines)

    def _positions_text(self) -> str:
        if not self.agent.active_positions:
            return "📭 <b>Nenhuma posicao aberta.</b>"

        # Busca ordens abertas reais da exchange para mostrar alvos reais
        exchange_orders = {}
        real_positions = {}
        try:
            for o in self.agent.hl.get_open_orders():
                coin = o.get("coin", "")
                if coin not in exchange_orders:
                    exchange_orders[coin] = []
                exchange_orders[coin].append(o)
        except Exception:
            pass

        try:
            if not config.DRY_RUN:
                real_positions = self.agent.hl.get_all_positions()
        except Exception:
            real_positions = {}

        lines = [f"📈 <b>Posicoes Abertas ({len(self.agent.active_positions)})</b>",
                 "────────────────────"]

        for sym, pos in self.agent.active_positions.items():
            cur  = self.agent.hl.get_mark_price(sym)
            il   = pos["direction"] == "LONG"
            entry = float(pos.get("entry", 0) or cur)
            pp   = ((cur - entry) / entry * 100) if il else ((entry - cur) / entry * 100)
            pl   = pp * pos["leverage"]
            # Preferir unrealized real
            pu = None
            if sym in real_positions:
                pu = float(real_positions[sym].get("unrealized_pnl", 0) or 0)
            else:
                notional_tmp = cur * float(pos.get("size", 0) or 0)
                pu = (pp / 100.0) * notional_tmp
            em   = "🟢" if pu >= 0 else "🔴"
            st   = "⏳ aguardando fill" if pos.get("status") == "pending_fill" else ""
            be   = "✅ breakeven atingido" if pos.get("tp1_hit") else ""

            # Monta alvos: usa estado interno OU busca da exchange
            sl   = float(pos.get("stop_loss", 0) or 0)
            tp1  = float(pos.get("tp1", 0) or 0)
            tp2  = float(pos.get("tp2", 0) or 0)

            # Se alvos estao zerados, busca das ordens abertas na exchange
            if (sl == 0 or tp1 == 0) and sym in exchange_orders:
                stops, limits = [], []
                for o in exchange_orders[sym]:
                    px = float(o.get("limitPx", 0))
                    ot = o.get("orderType", "")
                    if "Stop" in ot or "stop" in ot.lower() or o.get("triggerPx"):
                        stops.append(float(o.get("triggerPx", px) or px))
                    else:
                        limits.append(px)
                if stops:
                    sl = stops[0]
                limits_sorted = sorted(limits) if il else sorted(limits, reverse=True)
                if len(limits_sorted) >= 1: tp1 = limits_sorted[0]
                if len(limits_sorted) >= 2: tp2 = limits_sorted[1]

            sl_str  = f"<code>${sl:,.4f}</code>"  if sl  > 0 else "<code>—</code>"
            tp1_str = f"<code>${tp1:,.4f}</code>" if tp1 > 0 else "<code>—</code>"
            tp2_str = f"<code>${tp2:,.4f}</code>" if tp2 > 0 else "<code>—</code>"

            # Distancia ate SL e TP1
            sl_dist  = f" ({abs(cur-sl)/cur*100:.1f}%)"  if sl  > 0 else ""
            tp1_dist = f" ({abs(tp1-cur)/cur*100:.1f}%)" if tp1 > 0 else ""

            notional = cur * float(pos.get("size", 0) or 0)
            margin_calc = notional / float(pos.get("leverage", 1) or 1) if notional > 0 else 0
            margin_real = float(real_positions.get(sym, {}).get("margin_used", 0) or 0)
            margin_used = margin_real if margin_real > 0 else margin_calc

            lines.append(
                f"\n{em} <b>{sym}</b> | {'LONG' if il else 'SHORT'} {pos['leverage']}x {st}{be}\n"
                f"  Entrada: <code>${entry:,.4f}</code> → Atual: <code>${cur:,.4f}</code>\n"
                f"  Alocado: <code>${margin_used:,.2f}</code> | Notional: <code>${notional:,.2f}</code>\n"
                f"  P&amp;L: <code>{pl:+.2f}%</code> (<code>${pu:+.2f}</code>)\n"
                f"  🛑 SL: {sl_str}{sl_dist}\n"
                f"  🎯 TP1: {tp1_str}{tp1_dist}\n"
                f"  🏆 TP2: {tp2_str}"
            )

            # Lista ordens abertas na exchange para esse simbolo
            if sym in exchange_orders:
                lines.append(f"  📋 Ordens na exchange: <code>{len(exchange_orders[sym])}</code>")

        return "\n".join(lines)

    def _history_text(self) -> str:
        # Preferir o journal (append-only) quando existir
        events = []
        try:
            events = self.agent._journal.tail(n=10, event_type="trade_close")
        except Exception:
            events = []

        if events:
            lines = [f"📋 <b>Histórico (últimos {len(events)})</b>", "────────────────────"]
            for ev in events[::-1]:
                pnl = float(ev.get("pnl_usd", 0) or 0)
                em  = "✅ Lucro" if pnl >= 0 else "❌ Perda"
                lines.append(
                    f"{em} | <b>{ev.get('symbol','?')}</b> {ev.get('direction','?')}\n"
                    f"  Resultado: <code>${pnl:+.2f}</code> | {self._safe(ev.get('reason','?'))}"
                )
            return "\n".join(lines)

        hist = self.agent._state.trade_history
        if not hist:
            return "📭 <b>Nenhum trade fechado ainda.</b>"
        lines = [f"📋 <b>Histórico ({len(hist)} trades)</b>", "────────────────────"]
        for t in hist[-10:][::-1]:
            pnl = t.get("pnl_usd", 0)
            em  = "✅ Lucro" if pnl >= 0 else "❌ Perda"
            lines.append(
                f"{em} | <b>{t.get('symbol','?')}</b> {t.get('direction','?')} {t.get('leverage',1)}x\n"
                f"  Entrada: <code>${t.get('entry',0):,.4f}</code> → "
                f"Saída: <code>${t.get('exit',0):,.4f}</code>\n"
                f"  Resultado: <code>${pnl:+.2f}</code> | {t.get('exit_reason','?')}"
            )
        return "\n".join(lines)

    async def _do_analyze(self, update: Update, symbol: str):
        m = await update.message.reply_text(
            f"🔍 Analisando <b>{symbol}</b>...", parse_mode="HTML")
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self.agent.analyze_market, symbol)
            signal = result.get("signal")
            text   = self._format_signal(symbol, signal) if signal else f"Sem dados para {symbol}."
            await m.edit_text(text, parse_mode="HTML")
        except Exception as e:
            await m.edit_text(f"Erro: {e}")

    async def notify(self, chat_id: int, message: str):
        try:
            await self.app.bot.send_message(
                chat_id=chat_id, text=message, parse_mode="HTML")
        except Exception:
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=message)
            except Exception as e2:
                logger.error(f"Notify error: {e2}")

    def run(self):
        logger.info("TelegramBot run() chamado")