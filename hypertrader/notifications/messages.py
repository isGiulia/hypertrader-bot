"""
notifications/messages.py — Construtores de mensagens Telegram.

Princípio Clean Code: "Não misture lógica de negócio com formatação."
Toda formatação de texto para Telegram fica aqui como funções puras.
Funções puras são fáceis de testar e não têm efeitos colaterais.
"""
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from hypertrader import config

# Timezone de exibicao — configuravel via DISPLAY_TIMEZONE no .env
try:
    _DISPLAY_TZ = ZoneInfo(config.DISPLAY_TIMEZONE)
except Exception:
    _DISPLAY_TZ = timezone.utc


def _now_utc() -> str:
    """Retorna horario local formatado para exibicao no Telegram."""
    now_local = datetime.now(_DISPLAY_TZ)
    tz_name   = now_local.strftime("%Z")
    return now_local.strftime(f"%H:%M:%S {tz_name}")


def _pnl_emoji(value: float) -> str:
    return "✅" if value >= 0 else "❌"


# ── Startup

def startup_message() -> str:
    net = "TESTNET" if config.USE_TESTNET else "MAINNET"
    mode = "DRY RUN — Simulado" if config.DRY_RUN else "🔴 LIVE — Dinheiro real"
    symbols = ", ".join(config.SYMBOLS)
    lev_mode = getattr(config, "LEVERAGE_MODE", "auto")
    lev_cap = getattr(config, "LEVERAGE_CAP", "?")
    return (
        f"🚀 <b>HyperTrader ATIVO</b>\n────────────────────\n"
        f"🌐 {net}\n"
        f"⚙️ {mode}\n"
        f"🎯 Pares: <code>{symbols}</code>\n"
        f"💰 Risco/trade: <code>{config.RISK_PER_TRADE * 100:.2f}%</code> "
        f"| Lev: <code>{lev_mode}</code> (cap <code>{lev_cap}x</code>)\n"
        f"⏱ Intervalo: <code>{config.ANALYSIS_INTERVAL}s</code>"
    )


def sync_message(equity: float, real_pos: dict, open_orders: list,
                 fills_summary: dict, funding: dict) -> str:
    net  = "TESTNET" if config.USE_TESTNET else "MAINNET"
    mode = "DRY RUN" if config.DRY_RUN else "🔴 LIVE"
    msg  = (
        f"🔄 <b>Bot Reiniciado — Sincronizado</b>\n────────────────────\n"
        f"🌐 {net} | {mode}\n"
        f"💼 Capital: <code>${equity:,.2f}</code>\n"
        f"📊 Posições abertas: <code>{len(real_pos)}</code>\n"
        f"📋 Ordens abertas: <code>{len(open_orders)}</code>\n"
        f"💰 Fills totais: <code>{fills_summary['total_trades']}</code> | "
        f"Fees: <code>${fills_summary['total_fee']:.4f}</code>\n"
        f"📈 P&L realizado: <code>${fills_summary['total_pnl']:+.4f}</code>\n"
        f"💸 Funding 24h: <code>${funding['total_funding_usd']:+.4f}</code>\n"
    )
    if real_pos:
        msg += "────────────────────\n"
        for sym, rp in real_pos.items():
            em   = "🟢" if rp["is_long"] else "🔴"
            side = "LONG" if rp["is_long"] else "SHORT"
            msg += (
                f"{em} <b>{sym}</b> {side}\n"
                f"  Entrada: <code>${rp['entry_price']:,.4f}</code> | "
                f"P&L: <code>${rp['unrealized_pnl']:+.2f}</code> | "
                f"Liq: <code>${rp['liq_price']:,.4f}</code>\n"
            )
    if open_orders:
        msg += "────────────────────\n📋 <b>Ordens abertas:</b>\n"
        for o in open_orders[:8]:
            side = "BUY" if o.get("side") == "B" else "SELL"
            msg += f"  • {o.get('coin')} {side} @ <code>${float(o.get('limitPx', 0)):,.4f}</code> sz={o.get('sz')}\n"
    msg += f"\n🕐 {_now_utc()}"
    return msg


def account_snapshot_message(equity: float, margin_used: float, free_margin: float,
                             positions: dict, paused: bool,
                             daily_pnl: float, session_pnl: float,
                             daily_trades: int, max_trades: int) -> str:
    """Snapshot curto: capital + onde está alocado + PnL."""
    net  = "TESTNET" if config.USE_TESTNET else "MAINNET"
    mode = "DRY RUN" if config.DRY_RUN else "🔴 LIVE"
    st   = "⏸ PAUSADO" if paused else "✅ RODANDO"

    msg = (
        f"💼 <b>Snapshot da Conta</b>\n────────────────────\n"
        f"{st} | 🌐 {net} | {mode}\n\n"
        f"💰 Equity: <code>${equity:,.2f}</code>\n"
        f"📊 Margem usada: <code>${margin_used:,.2f}</code>\n"
        f"🆓 Livre: <code>${free_margin:,.2f}</code>\n\n"
        f"🔢 Trades hoje: <code>{daily_trades}/{max_trades}</code>\n"
        f"💵 P&L hoje: <code>${daily_pnl:+.2f}</code> | sessão: <code>${session_pnl:+.2f}</code>\n"
    )

    if positions:
        msg += "\n────────────────────\n📈 <b>Posições (exchange)</b>\n"
        for sym, rp in list(positions.items())[:6]:
            side = "LONG" if rp.get("is_long") else "SHORT"
            em = "🟢" if rp.get("is_long") else "🔴"
            entry = float(rp.get("entry_price", 0) or 0)
            liq = float(rp.get("liq_price", 0) or 0)
            upnl = float(rp.get("unrealized_pnl", 0) or 0)
            m_used = float(rp.get("margin_used", 0) or 0)
            msg += (
                f"{em} <b>{sym}</b> {side} | margem <code>${m_used:,.2f}</code> | uPnL <code>${upnl:+.2f}</code>\n"
                f"  entrada <code>${entry:,.4f}</code> | liq <code>${liq:,.4f}</code>\n"
            )
    else:
        msg += "\n📭 Sem posições abertas agora.\n"

    msg += f"\n🕐 {_now_utc()}"
    return msg


# ── Trade lifecycle

def entry_message(symbol: str, direction: str, leverage: int, confidence: int,
                  real_entry: float, size: float, equity: float,
                  sl: float, tp1: float, tp2: float, rr_ratio: float,
                  signal: dict, pos: dict) -> str:
    risk_pct = abs(real_entry - sl) / real_entry * 100 if real_entry > 0 else 0
    tp1_pct  = abs(tp1 - real_entry) / real_entry * 100 if real_entry > 0 else 0
    tp2_pct  = abs(tp2 - real_entry) / real_entry * 100 if real_entry > 0 else 0

    valor_usdc      = real_entry * size
    margem_usada    = valor_usdc / leverage
    risk_used       = float(pos.get("risk_per_trade_used", config.RISK_PER_TRADE) or config.RISK_PER_TRADE)
    risco_usdc      = equity * risk_used
    em              = "🟢" if direction == "LONG" else "🔴"
    mode_label      = "DRY RUN" if config.DRY_RUN else "🔴 LIVE"

    msg = (
        f"{em} <b>ENTRADA EXECUTADA — {direction}</b>\n────────────────────\n"
        f"Par: <code>{symbol}/USDC</code> | Lev: <code>{leverage}x</code> | Conf: <code>{confidence}%</code>\n\n"
        f"💼 Patrimônio atual: <code>${equity:,.2f} USDC</code>\n"
        f"💵 Preço executado: <code>${real_entry:,.4f}</code>\n"
        f"📦 Size: <code>{size}</code> contratos = <code>${valor_usdc:,.2f} USDC</code> notional\n"
        f"🏦 Margem usada: <code>${margem_usada:,.2f} USDC</code> ({margem_usada/equity*100:.1f}% do capital)\n"
        f"⚠️ Risco máximo: <code>${risco_usdc:,.2f} USDC</code> ({risk_used*100:.2f}% do capital)\n\n"
        f"🛑 SL:  <code>${sl:,.4f}</code> (-{risk_pct:.2f}%)\n"
        f"🎯 TP1: <code>${tp1:,.4f}</code> (+{tp1_pct:.2f}%)\n"
        f"🏆 TP2: <code>${tp2:,.4f}</code> (+{tp2_pct:.2f}%)\n\n"
        f"📐 R:R: <code>1:{rr_ratio}</code>\n"
    )

    if not config.DRY_RUN:
        msg += (
            f"\n🔗 IDs na Exchange:\n"
            f"  SL:  <code>{pos.get('sl_order_id', '?')}</code>\n"
            f"  TP1: <code>{pos.get('tp1_order_id', '?')}</code>\n"
            f"  TP2: <code>{pos.get('tp2_order_id', '?')}</code>\n"
        )

    if signal.get("ganho_maximo"):
        msg += f"💰 {signal['ganho_maximo']}\n"
    if signal.get("perda_maxima"):
        msg += f"⚠️ {signal['perda_maxima']}\n"
    if signal.get("reasoning_simples"):
        msg += f"\n💬 {signal['reasoning_simples']}\n"

    msg += f"\n🕐 {_now_utc()} | {mode_label}"
    return msg


def pending_fill_message(symbol: str, direction: str, exec_price: float,
                         size: float, order_id) -> str:
    return (
        f"⏳ <b>ORDEM ABERTA — {symbol}</b>\n────────────────────\n"
        f"Tipo: <code>LIMIT {direction}</code>\n"
        f"Preço: <code>${exec_price:,.4f}</code> | Size: <code>{size}</code>\n"
        f"ID: <code>{order_id}</code>\n"
        f"Status: <b>Aguardando preenchimento</b>\n"
        f"🕐 {_now_utc()}"
    )


def fill_confirmed_message(symbol: str, pos: dict) -> str:
    return (
        f"✅ <b>FILL CONFIRMADO — {symbol}</b>\n────────────────────\n"
        f"Entrada real: <code>${pos['entry']:,.4f}</code>\n"
        f"SL:  <code>{pos.get('sl_order_id', '?')}</code>\n"
        f"TP1: <code>{pos.get('tp1_order_id', '?')}</code>\n"
        f"TP2: <code>{pos.get('tp2_order_id', '?')}</code>\n"
        f"🕐 {_now_utc()}"
    )


def close_message(symbol: str, pos: dict, entry: float, exit_price: float,
                  pnl_lev: float, pnl_usd: float, dur_min: int,
                  reason: str, real_fee: float = 0, funding_usd: float = 0) -> str:
    em  = _pnl_emoji(pnl_usd)
    lbl = "LUCRO" if pnl_usd >= 0 else "PERDA"
    msg = (
        f"{em} <b>SAÍDA — {lbl}</b>\n────────────────────\n"
        f"<b>{symbol}</b> {pos['direction']} {pos.get('leverage', 1)}x\n"
        f"Motivo: <code>{reason}</code> | Duração: <code>{dur_min}min</code>\n\n"
        f"📥 Entrada: <code>${entry:,.4f}</code>\n"
        f"📤 Saída:   <code>${exit_price:,.4f}</code>\n\n"
        f"<b>Resultado: <code>{pnl_lev:+.2f}%</code> (<code>${pnl_usd:+.2f}</code>)</b>\n"
    )
    if real_fee:
        msg += f"💸 Fee pago: <code>${real_fee:.4f}</code>\n"
    if funding_usd:
        em_f = "📈" if funding_usd >= 0 else "📉"
        msg += f"{em_f} Funding: <code>${funding_usd:+.4f}</code>\n"
    msg += f"\n🕐 {_now_utc()}"
    return msg


def tp1_hit_message(symbol: str, pnl_lev: float, entry: float, tp2: float) -> str:
    return (
        f"🎯 <b>TP1 ATINGIDO — {symbol}</b>\n────────────────────\n"
        f"Resultado parcial: <code>+{pnl_lev:.2f}%</code>\n"
        f"SL movido para entrada: <code>${entry:,.4f}</code>\n"
        f"Próximo alvo TP2: <code>${tp2:,.4f}</code>\n"
        f"🕐 {_now_utc()}"
    )


def liquidation_alert(symbol: str, loss: float, liq_pct: float,
                      real_pnl_usd: Optional[float]) -> str:
    pnl_str = f"<code>${real_pnl_usd:+.2f}</code>" if real_pnl_usd is not None else "N/A"
    return (
        f"🚨 <b>ALERTA LIQUIDAÇÃO — {symbol}</b>\n"
        f"Perda: <code>{loss:.1f}%</code> (liquidação em ~{liq_pct:.1f}%)\n"
        f"P&L: {pnl_str}\n"
        f"Use /fechar {symbol} para fechar agora!\n"
        f"🕐 {_now_utc()}"
    )


# ── P&L Update

def pnl_update_message(positions: dict, real_pnls: dict,
                       daily_pnl: float, stats: dict) -> str:
    from hypertrader.exchange.hyperliquid_client import HyperliquidClient
    now  = _now_utc()
    total = stats["total"]
    wr    = round(stats["wins"] / total * 100, 1) if total > 0 else 0
    unr   = 0.0
    lines = [f"📊 <b>UPDATE — {now}</b>", "────────────────────"]

    for sym, pos in positions.items():
        from hypertrader.exchange import hyperliquid_client  # evita import circular
        is_long = pos["direction"] == "LONG"
        entry   = float(pos.get("entry", 0))
        # Mark price precisa vir de fora — simplifica para cálculo interno aqui
        # (o caller passa real_pnls com valores da exchange)
        pu      = real_pnls.get(sym, 0.0)
        unr    += pu
        em      = "🟢" if pu >= 0 else "🔴"
        sl      = float(pos.get("stop_loss", 0) or 0)
        tp1     = float(pos.get("tp1", 0) or 0)
        opened  = pos.get("opened_at", "")[:16].replace("T", " ")
        lines.append(
            f"{em} <b>{sym}</b> {'LONG' if is_long else 'SHORT'} {pos['leverage']}x\n"
            f"  Entrada: <code>${entry:,.4f}</code>\n"
            f"  P&L: <code>${pu:+.2f}</code>\n"
            f"  SL: <code>${sl:,.4f}</code> | TP1: <code>${tp1:,.4f}</code>\n"
            f"  Aberto: <code>{opened} UTC</code>"
        )

    lines += [
        "────────────────────",
        f"💰 Não realizado: <code>${unr:+.2f}</code>",
        f"💵 Realizado hoje: <code>${daily_pnl:+.2f}</code>",
        f"📈 Win Rate: <code>{wr}%</code> ({stats['wins']}W/{stats['losses']}L)",
        f"🕐 {now}",
    ]
    return "\n".join(lines)


# ── Cycle summary

def cycle_summary(results: list, equity: float, dd_pct: float, dd_status: str,
                  n_positions: int, daily_trades: int, daily_pnl: float,
                  session_pnl: float, stats: dict, next_time: str) -> str:
    total = stats["total"]
    wr    = round(stats["wins"] / total * 100, 1) if total > 0 else 0
    return (
        "🔄 <b>Ciclo Completo</b>\n────────────────────\n"
        + "\n".join(results)
        + "\n\n────────────────────\n"
        f"💼 Capital: <code>${equity:,.2f}</code>\n"
        f"📉 Drawdown: <code>{dd_pct:.1f}%</code> {dd_status}\n"
        f"📊 Posições: <code>{n_positions}/{config.MAX_POSITIONS}</code>\n"
        f"🔢 Trades hoje: <code>{daily_trades}/{config.MAX_TRADES_DAY}</code>\n"
        f"💵 P&L hoje: <code>${daily_pnl:+.2f}</code> | sessão: <code>${session_pnl:+.2f}</code>\n"
        f"🏆 Win Rate: <code>{wr}%</code> ({stats['wins']}W/{stats['losses']}L)\n"
        f"⏱ Próxima: <code>{next_time}</code>"
    )