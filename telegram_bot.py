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

logger = logging.getLogger("TelegramBot")

ALLOWED_IDS = [
    int(x.strip()) for x in os.getenv("TELEGRAM_ALLOWED_IDS", "0").split(",")
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
        a.add_handler(CommandHandler("relatorio",   self.cmd_relatorio))
        a.add_handler(CommandHandler("historico",   self.cmd_historico))
        a.add_handler(CommandHandler("pausar",      self.cmd_pausar))
        a.add_handler(CommandHandler("retomar",     self.cmd_retomar))
        a.add_handler(CommandHandler("fechar",      self.cmd_fechar))
        a.add_handler(CommandHandler("config",      self.cmd_config))
        a.add_handler(CommandHandler("alavancagem", self.cmd_alavancagem))
        a.add_handler(CommandHandler("ajuda",       self.cmd_ajuda))
        a.add_handler(CallbackQueryHandler(self.handle_callback))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_msg))

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
            [InlineKeyboardButton("📋 Relatorio",  callback_data="relatorio"),
             InlineKeyboardButton("⚙️ Config",      callback_data="config")],
            [InlineKeyboardButton("🔢 Alavancagem",callback_data="alavancagem"),
             InlineKeyboardButton("📖 Ajuda",      callback_data="ajuda")],
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
            f"Lev: <code>BTC/ETH=5x | SOL/AVAX=3x</code>\n\n"
            f"Use os botoes ou envie mensagem para o agente.",
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
        await m.edit_text(self._positions_text(), parse_mode="HTML")

    @auth
    async def cmd_analisar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        symbol = ctx.args[0].upper() if ctx.args else "BTC"
        await self._do_analyze(update, symbol)

    @auth
    async def cmd_relatorio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("📋 Gerando relatorio...")
        try:
            report = await asyncio.get_event_loop().run_in_executor(
                None, self.agent.generate_daily_report)
            await m.edit_text(
                f"📋 <b>RELATORIO DE SESSAO</b>\n\n{self._safe(report[:3800])}",
                parse_mode="HTML")
        except Exception as e:
            await m.edit_text(f"Erro: {e}")

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
        pos = self.agent.active_positions[sym]
        cur = self.agent.hl.get_mark_price(sym)
        self.agent._close_position(sym, pos, cur, "Manual via Telegram")
        await update.message.reply_text(
            f"Posicao <b>{sym}</b> fechada manualmente.", parse_mode="HTML")

    @auth
    async def cmd_config(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        
        lev_map = config.LEVERAGE_MAP
        lev_str = " | ".join(f"{k}={v}x" for k, v in lev_map.items())
        await update.message.reply_text(
            f"⚙️ <b>Configuracao Atual</b>\n"
            f"────────────────────\n"
            f"🌐 Rede: <code>{'MAINNET' if not config.USE_TESTNET else 'TESTNET'}</code>\n"
            f"⚙️ Modo: <code>{'LIVE 🔴' if not config.DRY_RUN else 'DRY RUN 🔵'}</code>\n"
            f"🎯 Pares: <code>{', '.join(config.SYMBOLS)}</code>\n"
            f"📊 Max posicoes: <code>{config.MAX_POSITIONS}</code>\n"
            f"💰 Risco/trade: <code>{config.RISK_PER_TRADE*100:.1f}%</code>\n"
            f"🛑 Stop diario: <code>{config.DAILY_LOSS_LIMIT*100:.0f}%</code>\n"
            f"🛡️ Drawdown half: <code>{config.DRAWDOWN_HALF}%</code> | stop: <code>{config.DRAWDOWN_STOP}%</code>\n"
            f"⏱ Intervalo: <code>{config.ANALYSIS_INTERVAL}s</code>\n"
            f"🤖 Confianca minima: <code>{config.MIN_CONFIDENCE}%</code>\n"
            f"🔢 Alavancagem: <code>{lev_str}</code>",
            parse_mode="HTML"
        )

    @auth
    async def cmd_alavancagem(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("⏳ Buscando...")
        try:
            lev_map = await asyncio.get_event_loop().run_in_executor(
                None, self.agent.hl.get_asset_info)
            
            lines = ["🔢 <b>Alavancagem por Moeda</b>", "────────────────────"]
            for sym in config.SYMBOLS:
                configured   = config.LEVERAGE_MAP.get(sym, config.LEVERAGE_FALLBACK)
                platform_max = lev_map.get(sym, {}).get("maxLeverage", "?")
                used = "🟢 " if sym in self.agent.active_positions else "   "
                lines.append(f"{used}<code>{sym:<6}</code> configurado: <code>{configured}x</code> | plataforma: <code>{platform_max}x</code>")
            lines.append(f"\n🕐 {datetime.now(_DISPLAY_TZ).strftime('%H:%M %Z')}")
            await m.edit_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await m.edit_text(f"Erro: {e}")

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
            "/relatorio — Relatorio completo da sessao\n"
            "/pausar — Parar novas analises\n"
            "/retomar — Retomar o bot\n"
            "/fechar BTC — Fechar posicao manualmente\n"
            "/config — Ver configuracoes atuais\n"
            "/alavancagem — Alavancagem por moeda\n\n"
            "💬 <b>Dica:</b> Envie qualquer mensagem para conversar com o agente!",
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
            elif d == "relatorio":
                await q.edit_message_text("📋 Gerando relatorio...")
                report = await asyncio.get_event_loop().run_in_executor(
                    None, self.agent.generate_daily_report)
                await q.edit_message_text(
                    f"📋 <b>RELATORIO</b>\n\n{self._safe(report[:3800])}", parse_mode="HTML")
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
            elif d == "config":
                lev_str = " | ".join(f"{k}={v}x" for k, v in config.LEVERAGE_MAP.items())
                await q.edit_message_text(
                    f"⚙️ <b>Config</b>\n"
                    f"Rede: <code>{'TESTNET' if config.USE_TESTNET else 'MAINNET'}</code>\n"
                    f"Modo: <code>{'DRY RUN' if config.DRY_RUN else 'LIVE 🔴'}</code>\n"
                    f"Pares: <code>{', '.join(config.SYMBOLS)}</code>\n"
                    f"Risco/trade: <code>{config.RISK_PER_TRADE*100:.1f}%</code>\n"
                    f"Max pos: <code>{config.MAX_POSITIONS}</code>\n"
                    f"Conf. minima: <code>{config.MIN_CONFIDENCE}%</code>\n"
                    f"Alavancagem: <code>{lev_str}</code>",
                    parse_mode="HTML")
            elif d == "alavancagem":
                lev_map = await asyncio.get_event_loop().run_in_executor(
                    None, self.agent.hl.get_asset_info)
                
                lines = ["🔢 <b>Alavancagem Maxima</b>", "────────────────────"]
                for sym in config.SYMBOLS:
                    configured   = config.LEVERAGE_MAP.get(sym, config.LEVERAGE_FALLBACK)
                    platform_max = lev_map.get(sym, {}).get("maxLeverage", "?")
                    used = "🟢 " if sym in self.agent.active_positions else "   "
                    lines.append(f"{used}<code>{sym:<5}</code> cfg: <code>{configured}x</code> plat: <code>{platform_max}x</code>")
                lines.append(f"\n🕐 {datetime.now(_DISPLAY_TZ).strftime('%H:%M %Z')}")
                await q.edit_message_text("\n".join(lines), parse_mode="HTML")
            elif d == "ajuda":
                await q.edit_message_text(
                    "📖 <b>Comandos:</b>\n"
                    "/status /pnl /posicoes /historico\n"
                    "/analisar BTC /pausar /retomar\n"
                    "/config /alavancagem /fechar BTC",
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
            free   = equity - margin
            
            total  = self.agent.stats["total"]
            wr     = round(self.agent.stats["wins"] / total * 100, 1) if total > 0 else 0
            dd_pct = self.agent.risk.get_drawdown_pct(equity)
            return (
                f"🤖 <b>HyperTrader — Status</b>\n"
                f"────────────────────\n"
                f"{'⏸ PAUSADO' if self.agent.paused else '✅ RODANDO'}\n"
                f"🌐 <code>{'TESTNET' if config.USE_TESTNET else 'MAINNET'}</code> | "
                f"<code>{'DRY RUN' if config.DRY_RUN else 'LIVE 🔴'}</code>\n\n"
                f"💰 <b>Equity:</b> <code>${equity:,.2f}</code>\n"
                f"📊 <b>Margin:</b> <code>${margin:,.2f}</code>\n"
                f"🆓 <b>Livre:</b> <code>${free:,.2f}</code>\n"
                f"📉 <b>Drawdown:</b> <code>{dd_pct:.1f}%</code>\n\n"
                f"📈 <b>Posicoes:</b> <code>{len(self.agent.active_positions)}/{config.MAX_POSITIONS}</code>\n"
                f"🔢 <b>Trades hoje:</b> <code>{self.agent.daily_trades_count}/{config.MAX_TRADES_DAY}</code>\n"
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
        lines = [
            f"💰 <b>P&amp;L — {datetime.now(_DISPLAY_TZ).strftime('%H:%M %Z')}</b>",
            "────────────────────"
        ]
        if self.agent.active_positions:
            lines.append("📈 <b>Posicoes Abertas:</b>")
            for sym, pos in self.agent.active_positions.items():
                cur  = self.agent.hl.get_mark_price(sym)
                il   = pos["direction"] == "LONG"
                pp   = ((cur - pos["entry"]) / pos["entry"] * 100) if il \
                       else ((pos["entry"] - cur) / pos["entry"] * 100)
                pl   = pp * pos["leverage"]
                pu   = (pp / 100) * pos["equity_open"]
                unr  += pu
                em   = "🟢" if pu >= 0 else "🔴"
                st   = "⏳" if pos.get("status") == "pending_fill" else ""
                lines.append(
                    f"\n{em}{st} <b>{sym}</b> | {'LONG' if il else 'SHORT'} {pos['leverage']}x\n"
                    f"  Entrada: <code>${pos['entry']:,.4f}</code> → <code>${cur:,.4f}</code>\n"
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
        lines = [f"📈 <b>Posicoes Abertas ({len(self.agent.active_positions)})</b>",
                 "────────────────────"]
        for sym, pos in self.agent.active_positions.items():
            cur     = self.agent.hl.get_mark_price(sym)
            il      = pos["direction"] == "LONG"
            pp      = ((cur - pos["entry"]) / pos["entry"] * 100) if il \
                      else ((pos["entry"] - cur) / pos["entry"] * 100)
            pl      = pp * pos["leverage"]
            pu      = (pp / 100) * pos["equity_open"]
            em      = "🟢" if pu >= 0 else "🔴"
            st      = "⏳ aguardando fill" if pos.get("status") == "pending_fill" else ""
            be      = "✅ breakeven" if pos.get("tp1_hit") else ""
            lines.append(
                f"\n{em} <b>{sym}</b> | {'LONG' if il else 'SHORT'} {pos['leverage']}x {st}{be}\n"
                f"  Entrada: <code>${pos['entry']:,.4f}</code> → <code>${cur:,.4f}</code>\n"
                f"  P&amp;L: <code>{pl:+.2f}%</code> (<code>${pu:+.2f}</code>)\n"
                f"  SL: <code>${pos['stop_loss']:,.4f}</code> | TP1: <code>${pos['tp1']:,.4f}</code> | TP2: <code>${pos['tp2']:,.4f}</code>"
            )
        return "\n".join(lines)

    def _history_text(self) -> str:
        hist = self.agent.trade_history
        if not hist:
            return "📭 <b>Nenhum trade fechado ainda.</b>"
        lines = [f"📋 <b>Historico ({len(hist)} trades)</b>", "────────────────────"]
        for t in hist[-10:][::-1]:
            pnl = t.get("pnl_usd", 0)
            em  = "✅ Lucro" if pnl >= 0 else "❌ Perda"
            lines.append(
                f"{em} | <b>{t.get('symbol','?')}</b> {t.get('direction','?')} {t.get('leverage',1)}x\n"
                f"  Entrada: <code>${t.get('entry',0):,.4f}</code> → "
                f"Saida: <code>${t.get('exit',0):,.4f}</code>\n"
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