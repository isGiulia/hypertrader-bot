"""
📱 HyperTrader Telegram Bot
Interface completa: comandos, botões, notificações de trade e P&L em tempo real
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone

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

# ═════════════════════════════════════════════════════════════════════════════
#  Auth decorator
# ═════════════════════════════════════════════════════════════════════════════
def auth(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if ALLOWED_IDS and uid not in ALLOWED_IDS:
            await update.message.reply_text("⛔ Acesso não autorizado.")
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


# ═════════════════════════════════════════════════════════════════════════════
#  BOT
# ═════════════════════════════════════════════════════════════════════════════
class TelegramBot:

    def __init__(self, agent, token: str):
        self.agent = agent
        self.token = token
        self.app   = Application.builder().token(token).build()
        self._register()
        logger.info("📱 TelegramBot inicializado")

    def _register(self):
        a = self.app
        a.add_handler(CommandHandler("start",      self.cmd_start))
        a.add_handler(CommandHandler("status",     self.cmd_status))
        a.add_handler(CommandHandler("posicoes",   self.cmd_posicoes))
        a.add_handler(CommandHandler("pnl",        self.cmd_pnl))
        a.add_handler(CommandHandler("analisar",   self.cmd_analisar))
        a.add_handler(CommandHandler("relatorio",  self.cmd_relatorio))
        a.add_handler(CommandHandler("historico",  self.cmd_historico))
        a.add_handler(CommandHandler("pausar",     self.cmd_pausar))
        a.add_handler(CommandHandler("retomar",    self.cmd_retomar))
        a.add_handler(CommandHandler("fechar",     self.cmd_fechar))
        a.add_handler(CommandHandler("config",     self.cmd_config))
        a.add_handler(CommandHandler("ajuda",      self.cmd_ajuda))
        a.add_handler(CommandHandler("alavancagem", self.cmd_alavancagem))
        a.add_handler(CallbackQueryHandler(self.handle_callback))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_msg))

    # ─────────────────────────────────────────────────────────────────────────
    #  Comandos
    # ─────────────────────────────────────────────────────────────────────────

    @auth
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        kb = [
            [InlineKeyboardButton("📊 Status",        callback_data="status"),
             InlineKeyboardButton("💰 P&L",            callback_data="pnl")],
            [InlineKeyboardButton("📈 Posições",       callback_data="posicoes"),
             InlineKeyboardButton("📋 Histórico",      callback_data="historico")],
            [InlineKeyboardButton("🔍 Analisar BTC",   callback_data="analisar_BTC"),
             InlineKeyboardButton("🔍 Analisar ETH",   callback_data="analisar_ETH")],
            [InlineKeyboardButton("🔍 Analisar SOL",   callback_data="analisar_SOL"),
             InlineKeyboardButton("🔍 Analisar WIF",   callback_data="analisar_WIF")],
            [InlineKeyboardButton("📋 Relatório",      callback_data="relatorio"),
             InlineKeyboardButton("⚙️ Config",         callback_data="config")],
            [InlineKeyboardButton("🔢 Alavancagens",   callback_data="alavancagem"),
             InlineKeyboardButton("📋 Relatório",      callback_data="relatorio")],
            [InlineKeyboardButton("⏸ Pausar",         callback_data="pausar"),
             InlineKeyboardButton("▶️ Retomar",        callback_data="retomar")],
        ]
        from main import CONFIG
        modo = "🔴 LIVE" if not CONFIG["dry_run"] else "🔵 DRY RUN"
        rede = "MAINNET" if not CONFIG["use_testnet"] else "TESTNET"
        await update.message.reply_text(
            f"🤖 *HyperTrader AI Agent*\n"
            f"{'─'*30}\n"
            f"Rede: `{rede}` | Modo: `{modo}`\n"
            f"Pares: `{', '.join(CONFIG['symbols'][:5])}...`\n\n"
            f"Use os botões ou envie qualquer mensagem para falar com o agente.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    @auth
    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("⏳ Buscando...")
        await m.edit_text(await self._status_text(), parse_mode="Markdown")

    @auth
    async def cmd_pnl(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("⏳ Calculando P&L...")
        await m.edit_text(await self._pnl_text(), parse_mode="Markdown")

    @auth
    async def cmd_posicoes(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("⏳ Carregando...")
        await m.edit_text(self._positions_text(), parse_mode="Markdown")

    @auth
    async def cmd_pnl(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("⏳ Calculando...")
        await m.edit_text(await self._pnl_text(), parse_mode="Markdown")

    @auth
    async def cmd_analisar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        symbol = ctx.args[0].upper() if ctx.args else "BTC"
        await self._do_analyze(update, symbol)

    @auth
    async def cmd_relatorio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        m = await update.message.reply_text("📋 Gerando relatório...")
        try:
            report = await asyncio.get_event_loop().run_in_executor(
                None, self.agent.generate_daily_report
            )
            await m.edit_text(
                f"📋 *RELATÓRIO DE SESSÃO*\n\n{report[:4000]}",
                parse_mode="Markdown"
            )
        except Exception as e:
            await m.edit_text(f"❌ Erro: {e}")

    @auth
    async def cmd_historico(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._history_text(), parse_mode="Markdown")

    @auth
    async def cmd_pausar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        self.agent.paused = True
        await update.message.reply_text("⏸ *Bot pausado.* Novas análises suspensas.", parse_mode="Markdown")

    @auth
    async def cmd_retomar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        self.agent.paused = False
        await update.message.reply_text("▶️ *Bot retomado!*", parse_mode="Markdown")

    @auth
    async def cmd_fechar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not ctx.args:
            await update.message.reply_text("Uso: /fechar BTC")
            return
        sym = ctx.args[0].upper()
        if sym not in self.agent.active_positions:
            await update.message.reply_text(f"Sem posição aberta em {sym}.")
            return
        pos = self.agent.active_positions[sym]
        cur = self.agent.hl.get_mark_price(sym)
        self.agent._close_position(sym, pos, cur, "Manual via Telegram")
        await update.message.reply_text(f"✅ Posição *{sym}* fechada manualmente.", parse_mode="Markdown")

    @auth
    async def cmd_config(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from main import CONFIG
        text = (
            f"⚙️ *Configuração Atual*\n{'─'*28}\n"
            f"🌐 Rede: `{'MAINNET' if not CONFIG['use_testnet'] else 'TESTNET'}`\n"
            f"⚙️ Modo: `{'LIVE 🔴' if not CONFIG['dry_run'] else 'DRY RUN 🔵'}`\n"
            f"🎯 Pares: `{len(CONFIG['symbols'])} símbolos`\n"
            f"📊 Max posições: `{CONFIG['max_positions']}`\n"
            f"💰 Risco/trade: `{CONFIG['risk_per_trade']*100:.1f}%`\n"
            f"🔢 Alavancagem: `até {CONFIG['max_leverage']}x`\n"
            f"🛑 Stop diário: `{CONFIG['daily_loss_limit']*100:.0f}%`\n"
            f"⏱ Intervalo: `{CONFIG['analysis_interval']}s`\n"
            f"🤖 Conf. mínima: `{CONFIG['min_confidence']}%`\n"
            f"\n"
            f"🔍 Pares monitorados:\n"
            f"`{', '.join(CONFIG['symbols'])}`"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    @auth
    async def cmd_alavancagem(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Mostra alavancagem máxima real de cada símbolo (via API Hyperliquid)"""
        m = await update.message.reply_text("⏳ Buscando limites da plataforma...")
        try:
            lev_map = await asyncio.get_event_loop().run_in_executor(
                None, self.agent.hl.get_asset_info
            )
            from main import CONFIG
            lines = [f"🔢 *Alavancagem Máxima — Hyperliquid*", f"{'─'*32}"]
            for sym in CONFIG["symbols"]:
                info = lev_map.get(sym, {})
                lev  = info.get("maxLeverage", "?")
                only_iso = info.get("onlyIsolated", False)
                iso_label = " ⚠️ somente isolado" if only_iso else ""
                lines.append(f"  `{sym:<6}` → `{lev}x`{iso_label}")
            lines += [
                f"{'─'*32}",
                f"ℹ️ Fonte: API Hyperliquid em tempo real",
                f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
            ]
            await m.edit_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await m.edit_text(f"❌ Erro: {e}")

    @auth
    async def cmd_ajuda(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📖 *Comandos:*\n\n"
            "/start — Menu principal\n"
            "/status — Status do bot e conta\n"
            "/pnl — P&L detalhado das posições\n"
            "/posicoes — Posições abertas\n"
            "/historico — Histórico de trades\n"
            "/analisar BTC — Analisar símbolo\n"
            "/relatorio — Relatório de performance\n"
            "/pausar — Pausar bot autônomo\n"
            "/retomar — Retomar bot\n"
            "/fechar BTC — Fechar posição manualmente\n"
            "/config — Configurações\n"
            "/alavancagem — Limites de alavancagem por moeda\n"
            "/ajuda — Esta mensagem\n\n"
            "💬 Envie qualquer texto para conversar com o agente AI!",
            parse_mode="Markdown"
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Callbacks de botões
    # ─────────────────────────────────────────────────────────────────────────

    @auth
    async def handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        d = q.data

        try:
            if d == "status":
                await q.edit_message_text(await self._status_text(), parse_mode="Markdown")
            elif d == "pnl":
                await q.edit_message_text(await self._pnl_text(), parse_mode="Markdown")
            elif d == "posicoes":
                await q.edit_message_text(self._positions_text(), parse_mode="Markdown")
            elif d == "historico":
                await q.edit_message_text(self._history_text(), parse_mode="Markdown")
            elif d == "relatorio":
                await q.edit_message_text("📋 Gerando relatório...")
                report = await asyncio.get_event_loop().run_in_executor(
                    None, self.agent.generate_daily_report
                )
                await q.edit_message_text(f"📋 *RELATÓRIO*\n\n{report[:4000]}", parse_mode="Markdown")
            elif d.startswith("analisar_"):
                sym = d.split("_", 1)[1]
                await q.edit_message_text(f"🔍 Analisando *{sym}*...", parse_mode="Markdown")
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self.agent.analyze_market, sym
                )
                text   = result["analysis"]
                signal = result.get("signal")
                if len(text) > 3400:
                    text = text[:3400] + "\n...[truncado]"
                reply = f"📈 *{sym}*\n\n{text}"
                markup = None
                if signal and signal.get("signal") in ("LONG", "SHORT"):
                    conf = signal.get("confidence", 0)
                    em   = "🟢" if signal["signal"] == "LONG" else "🔴"
                    reply += (
                        f"\n\n{em} *{signal['signal']}* | Conf: `{conf}%`\n"
                        f"Entry: `${signal.get('entry_price',0):,.4f}` | "
                        f"SL: `${signal.get('stop_loss',0):,.4f}`"
                    )
                    markup = InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"⚡ Executar {signal['signal']}", callback_data=f"exec_{sym}"),
                        InlineKeyboardButton("❌ Ignorar", callback_data="ignorar")
                    ]])
                await q.edit_message_text(reply, parse_mode="Markdown", reply_markup=markup)

            elif d.startswith("exec_"):
                sym = d.split("_", 1)[1]
                await q.edit_message_text(f"⚡ Executando *{sym}*...", parse_mode="Markdown")
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self.agent.analyze_market, sym
                )
                s = result.get("signal")
                if s and s.get("signal") in ("LONG", "SHORT"):
                    exec_r = await asyncio.get_event_loop().run_in_executor(
                        None, self.agent.execute_trade, sym, s
                    )
                    await q.edit_message_text(
                        f"✅ Trade *{sym}* — `{exec_r.get('status')}`",
                        parse_mode="Markdown"
                    )
                else:
                    await q.edit_message_text("⚠️ Sinal expirado. Nenhum trade executado.")

            elif d == "pausar":
                self.agent.paused = True
                await q.edit_message_text("⏸ *Bot pausado.*", parse_mode="Markdown")
            elif d == "retomar":
                self.agent.paused = False
                await q.edit_message_text("▶️ *Bot retomado!*", parse_mode="Markdown")
            elif d == "config":
                from main import CONFIG
                await q.edit_message_text(
                    f"⚙️ *Config*\nPares: `{len(CONFIG['symbols'])}` | "
                    f"MaxPos: `{CONFIG['max_positions']}` | "
                    f"Risco: `{CONFIG['risk_per_trade']*100:.1f}%` | "
                    f"Lev: `{CONFIG['max_leverage']}x`",
                    parse_mode="Markdown"
                )
            elif d == "ignorar":
                await q.edit_message_text("❌ Sinal ignorado.")
            elif d == "alavancagem":
                lev_map = await asyncio.get_event_loop().run_in_executor(
                    None, self.agent.hl.get_asset_info
                )
                from main import CONFIG
                lines = ["🔢 *Alavancagem Máxima por Moeda*", "─"*30]
                for sym in CONFIG["symbols"]:
                    info = lev_map.get(sym, {})
                    lev  = info.get("maxLeverage", "?")
                    used = sym in self.agent.active_positions
                    tag  = " 🟢" if used else ""
                    lines.append(f"  `{sym:<6}` → `{lev}x`{tag}")
                lines.append(f"\n🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
                await q.edit_message_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Callback error [{d}]: {e}")
            try:
                await q.edit_message_text(f"❌ Erro: {e}")
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    #  Chat livre com o agente
    # ─────────────────────────────────────────────────────────────────────────

    @auth
    async def handle_msg(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        m    = await update.message.reply_text("🤔 Pensando...")
        try:
            resp = await asyncio.get_event_loop().run_in_executor(
                None, self.agent._chat, text
            )
            if len(resp) > 4000:
                resp = resp[:4000] + "\n...[truncado]"
            await m.edit_text(resp, parse_mode="Markdown")
        except Exception as e:
            await m.edit_text(f"❌ Erro: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    #  Helpers de texto
    # ─────────────────────────────────────────────────────────────────────────

    async def _status_text(self) -> str:
        try:
            acc = await asyncio.get_event_loop().run_in_executor(
                None, self.agent.hl.get_account_info
            )
            equity = acc.get("equity", 0)
            margin = acc.get("margin_used", 0)
            free   = equity - margin
            from main import CONFIG
            total  = self.agent.stats["total"]
            wr     = round(self.agent.stats["wins"] / total * 100, 1) if total > 0 else 0
            return (
                f"🤖 *HyperTrader — Status*\n{'─'*30}\n"
                f"{'⏸ PAUSADO' if self.agent.paused else '✅ RODANDO'}\n"
                f"🌐 `{'TESTNET' if CONFIG['use_testnet'] else 'MAINNET'}` | "
                f"`{'DRY RUN' if CONFIG['dry_run'] else '🔴 LIVE'}`\n\n"
                f"💰 *Equity:* `${equity:,.2f}`\n"
                f"📊 *Margin:* `${margin:,.2f}`\n"
                f"🆓 *Free:* `${free:,.2f}`\n\n"
                f"📈 *Posições abertas:* `{len(self.agent.active_positions)}/{CONFIG['max_positions']}`\n"
                f"💵 *P&L Sessão:* `${self.agent.session_pnl:+.2f}`\n"
                f"🏆 *Win Rate:* `{wr}%` ({self.agent.stats['wins']}W/{self.agent.stats['losses']}L)\n"
                f"📋 *Trades total:* `{total}`\n\n"
                f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            )
        except Exception as e:
            return f"❌ Erro ao buscar status: {e}"

    async def _pnl_text(self) -> str:
        total  = self.agent.stats["total"]
        wr     = round(self.agent.stats["wins"] / total * 100, 1) if total > 0 else 0
        lines  = [f"💰 *P&L DETALHADO — {datetime.now(timezone.utc).strftime('%H:%M UTC')}*", f"{'─'*32}"]
        unreal = 0.0

        if self.agent.active_positions:
            lines.append("📈 *Posições Abertas:*")
            for sym, pos in self.agent.active_positions.items():
                cur = self.agent.hl.get_mark_price(sym)
                il  = pos["direction"] == "LONG"
                pp  = ((cur - pos["entry"]) / pos["entry"] * 100) if il \
                      else ((pos["entry"] - cur) / pos["entry"] * 100)
                pl  = pp * pos["leverage"]
                pu  = (pp / 100) * pos["equity_open"]
                unreal += pu
                em = "🟢" if pu >= 0 else "🔴"
                dl = "LONG" if il else "SHORT"
                tp_status = "✅TP1 atingido" if pos.get("tp1_hit") else f"TP1: ${pos['tp1']:,.4f}"
                lines.append(
                    f"\n{em} *{sym}* | {dl} {pos['leverage']}x\n"
                    f"  Entrada: `${pos['entry']:,.4f}` → Atual: `${cur:,.4f}`\n"
                    f"  P&L: `{pl:+.2f}%` (`${pu:+.2f}`)\n"
                    f"  SL: `${pos['stop_loss']:,.4f}` | {tp_status}"
                )
        else:
            lines.append("📭 Nenhuma posição aberta")

        lines += [
            f"\n{'─'*32}",
            f"📈 *Não realizado:* `${unreal:+.2f}`",
            f"💵 *Realizado (sessão):* `${self.agent.session_pnl:+.2f}`",
            f"💰 *P&L Total:* `${(self.agent.session_pnl + unreal):+.2f}`",
            f"{'─'*16}",
            f"🏆 *Win Rate:* `{wr}%` ({self.agent.stats['wins']}W / {self.agent.stats['losses']}L)",
            f"🏅 *Melhor trade:* `${self.agent.stats['best']:+.2f}`",
            f"😢 *Pior trade:* `${self.agent.stats['worst']:+.2f}`",
            f"📋 *Total trades:* `{total}`",
        ]
        return "\n".join(lines)

    def _positions_text(self) -> str:
        if not self.agent.active_positions:
            return "📭 *Nenhuma posição aberta.*"
        lines = [f"📈 *Posições Abertas ({len(self.agent.active_positions)})*", f"{'─'*30}"]
        for sym, pos in self.agent.active_positions.items():
            cur    = self.agent.hl.get_mark_price(sym)
            il     = pos["direction"] == "LONG"
            pnl_pct = ((cur - pos["entry"]) / pos["entry"] * 100) if il \
                      else ((pos["entry"] - cur) / pos["entry"] * 100)
            pnl_lev = pnl_pct * pos["leverage"]
            pnl_usd = (pnl_pct / 100) * pos["equity_open"]
            em = "🟢" if pnl_usd >= 0 else "🔴"
            lines.append(
                f"\n{em} *{sym}* | {'LONG' if il else 'SHORT'} {pos['leverage']}x\n"
                f"  Entrada: `${pos['entry']:,.4f}` → `${cur:,.4f}`\n"
                f"  P&L: `{pnl_lev:+.2f}%` (`${pnl_usd:+.2f}`)\n"
                f"  SL: `${pos['stop_loss']:,.4f}` | TP1: `${pos['tp1']:,.4f}`\n"
                f"  Aberta: `{pos['opened_at'][:16].replace('T',' ')}`"
            )
        return "\n".join(lines)

    def _history_text(self) -> str:
        hist = self.agent.trade_history
        if not hist:
            return "📭 *Nenhum trade fechado ainda.*"
        lines = [f"📋 *Histórico de Trades ({len(hist)})*", f"{'─'*30}"]
        for t in hist[-10:][::-1]:   # últimos 10, mais recente primeiro
            sym    = t.get("symbol", "?")
            dir_   = t.get("direction", "?")
            entry  = t.get("entry", 0)
            exit_  = t.get("exit", 0)
            pnl    = t.get("pnl_usd", 0)
            reason = t.get("exit_reason", "?")
            em     = "✅" if pnl >= 0 else "❌"
            lines.append(
                f"{em} *{sym}* {dir_} | `${entry:,.4f}` → `${exit_:,.4f}`\n"
                f"   P&L: `${pnl:+.2f}` | {reason}"
            )
        return "\n".join(lines)

    async def _do_analyze(self, update: Update, symbol: str):
        m = await update.message.reply_text(f"🔍 Analisando *{symbol}*...", parse_mode="Markdown")
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self.agent.analyze_market, symbol
            )
            text   = result["analysis"]
            signal = result.get("signal")
            if len(text) > 3400:
                text = text[:3400] + "\n...[truncado]"
            reply  = f"📈 *Análise {symbol}*\n\n{text}"
            markup = None
            if signal and signal.get("signal") in ("LONG", "SHORT"):
                conf = signal.get("confidence", 0)
                em   = "🟢" if signal["signal"] == "LONG" else "🔴"
                reply += (
                    f"\n\n{em} *{signal['signal']}* | Conf: `{conf}%`\n"
                    f"Entry: `${signal.get('entry_price', 0):,.4f}` | "
                    f"SL: `${signal.get('stop_loss', 0):,.4f}` | "
                    f"TP1: `${signal.get('take_profit_1', 0):,.4f}`"
                )
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"⚡ Executar {signal['signal']}", callback_data=f"exec_{symbol}"),
                    InlineKeyboardButton("❌ Ignorar", callback_data="ignorar")
                ]])
            await m.edit_text(reply, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            await m.edit_text(f"❌ Erro: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    #  Notificação proativa (chamada pelo agente)
    # ─────────────────────────────────────────────────────────────────────────

    async def notify(self, chat_id: int, message: str):
        try:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Notify error: {e}")

    def run(self):
        logger.info("📱 Telegram polling iniciado")
        self.app.run_polling(drop_pending_updates=True)
