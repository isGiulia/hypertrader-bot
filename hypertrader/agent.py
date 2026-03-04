"""
agent.py — HyperTraderAgent: orquestrador central do bot.

Princípio Clean Code: "Um orquestrador não deve ter lógica — ele delega."
Esta classe é o "maestro": coordena os módulos mas não implementa nada.
Cada responsabilidade real fica no módulo especializado.
"""
import asyncio
import logging
from datetime import datetime, timezone

from hypertrader import config
from hypertrader.ai.claude_client import ClaudeClient
from hypertrader.core.state_manager import StateManager, TradingState
from hypertrader.core.signal_validator import SignalValidator
from hypertrader.core.trade_executor import TradeExecutor
from hypertrader.core.position_monitor import PositionMonitor
from hypertrader.exchange.hyperliquid_client import HyperliquidClient
from hypertrader.notifications import messages

logger = logging.getLogger("HyperTrader")


class HyperTraderAgent:

    def __init__(self):
        # ── Infraestrutura
        self._hl           = HyperliquidClient(config.HL_WALLET_ADDRESS, config.HL_PRIVATE_KEY, config.USE_TESTNET)
        self._claude       = ClaudeClient(notify_fn=self._notify)
        self._state_mgr    = StateManager()
        self._validator    = SignalValidator()
        self._executor     = TradeExecutor(self._hl, notify_fn=self._notify)

        # ── Módulos que importam market_analyzer e risk_manager originais
        from market_analyzer import MarketAnalyzer
        from risk_manager    import RiskManager
        self._analyzer     = MarketAnalyzer(self._hl)
        self._risk         = RiskManager(vars(config))

        # ── Estado
        self._state        = self._state_mgr.load()
        self._current_day  = datetime.now(timezone.utc).date()
        self._telegram     = None

        # ── Monitor (criado depois do estado — precisa de callbacks)
        self._monitor = PositionMonitor(
            exchange         = self._hl,
            executor         = self._executor,
            risk_manager     = self._risk,
            notify_fn        = self._notify,
            on_position_closed = self._on_position_closed,
            on_state_changed   = self._save_state,
        )

        # ── Alavancagem máxima por símbolo (carregada da exchange)
        self._leverage_limits: dict = {}
        self._load_leverage_limits()

        # ── Sincroniza com exchange ao iniciar (apenas LIVE)
        if not config.DRY_RUN:
            self._sync_with_exchange()

        logger.info(
            f"HyperTrader iniciado | {config.SYMBOLS} | "
            f"{'TESTNET' if config.USE_TESTNET else 'MAINNET'} | "
            f"{'DRY RUN' if config.DRY_RUN else 'LIVE'}"
        )

    # ── Properties para compatibilidade com telegram_bot.py existente

    @property
    def active_positions(self) -> dict:
        return self._state.active_positions

    @property
    def stats(self) -> dict:
        return self._state.stats

    @property
    def session_pnl(self) -> float:
        return self._state.session_pnl

    @property
    def daily_pnl(self) -> float:
        return self._state.daily_pnl

    @property
    def paused(self) -> bool:
        return getattr(self, "_paused", False)

    @paused.setter
    def paused(self, value: bool):
        self._paused = value

    @property
    def hl(self):
        return self._hl

    # ── Loop principal

    async def run_autonomous(self):
        logger.info("Loop autônomo iniciado")
        self._notify(messages.startup_message())

        pnl_tick = 0
        while True:
            try:
                if self.paused:
                    await asyncio.sleep(30)
                    continue

                self._check_new_day()
                self._check_daily_loss_limit()

                self._monitor.check_all(self._state.active_positions)
                pnl_tick += 10
                if pnl_tick >= config.PNL_UPDATE_INTERVAL:
                    self._notify_pnl_update()
                    pnl_tick = 0

                acc_val = self._hl.get_account_info().get("equity", 0)
                self._risk.update_peak(acc_val)
                metrics = self._risk.get_risk_metrics(acc_val, self._state.active_positions)

                results = await self._run_analysis_cycle()

                next_ts  = datetime.now(timezone.utc).timestamp() + config.ANALYSIS_INTERVAL
                next_str = datetime.fromtimestamp(next_ts, tz=timezone.utc).strftime("%H:%M UTC")

                self._notify(messages.cycle_summary(
                    results, acc_val,
                    metrics["drawdown_pct"], metrics["drawdown_status"],
                    len(self._state.active_positions),
                    self._state.daily_trades_count,
                    self._state.daily_pnl, self._state.session_pnl,
                    self._state.stats, next_str
                ))

                logger.info(
                    f"Ciclo | pos={len(self._state.active_positions)} "
                    f"| pnl=${self._state.session_pnl:+.2f} | prx={next_str}"
                )

                # Monitora SL/TP a cada 30s durante o intervalo de espera
                await self._wait_with_monitoring(config.ANALYSIS_INTERVAL, pnl_tick)

            except Exception as e:
                logger.error(f"Erro loop: {e}", exc_info=True)
                self._notify(f"🚨 <b>Erro loop</b>\n<code>{str(e)[:200]}</code>")
                await asyncio.sleep(30)

    # ── Análise e execução

    def analyze_market(self, symbol: str) -> dict:
        logger.info(f"Analisando {symbol}...")
        data     = self._analyzer.get_full_analysis(symbol)
        leverage = self._get_leverage(symbol)
        analysis = self._claude.ask(
            f"{symbol}/USDC perp Hyperliquid. Alavancagem={leverage}x. "
            f"NAO inclua leverage no JSON. Retorne APENAS o JSON.",
            data, symbol=symbol
        )
        signal = self._claude.extract_signal(analysis)
        if signal:
            signal["symbol"]   = symbol
            signal["leverage"] = leverage
            logger.info(f"{symbol}: {signal.get('signal')} conf={signal.get('confidence')}% lev={leverage}x")
        return {"symbol": symbol, "analysis": analysis, "signal": signal}

    def execute_trade(self, symbol: str, signal: dict) -> dict:
        if not signal or signal.get("signal") == "WAIT":
            return {"status": "skipped"}

        if self._state.daily_trades_count >= config.MAX_TRADES_DAY:
            return {"status": "blocked", "reason": f"Limite {config.MAX_TRADES_DAY} trades/dia"}

        direction  = signal["signal"]
        entry      = float(signal.get("entry_price", 0))
        sl         = float(signal.get("stop_loss", 0))
        tp1        = float(signal.get("take_profit_1", 0))
        tp2        = float(signal.get("take_profit_2", 0))
        confidence = signal.get("confidence", 0)
        leverage   = self._get_leverage(symbol)

        valid, reason = self._validator.validate(direction, entry, sl, tp1, tp2)
        if not valid:
            logger.warning(f"Preços inválidos [{symbol}]: {reason}")
            self._notify(f"⚠️ <b>Sinal rejeitado — {symbol}</b>\n{reason}")
            return {"status": "invalid_prices", "reason": reason}

        risk_d   = abs(entry - sl)
        reward_d = abs(tp1 - entry)
        rr       = reward_d / risk_d if risk_d > 0 else 0
        if rr < config.TAKE_PROFIT_RATIO:
            return {"status": "bad_rr", "reason": f"R:R {rr:.2f} < {config.TAKE_PROFIT_RATIO}"}

        acc        = self._hl.get_account_info()
        equity     = acc.get("equity", 0)
        if equity <= 0:
            return {"status": "error", "reason": "Equity zero"}

        self._risk.update_peak(equity)

        free_margin  = acc.get("free_margin", equity)
        min_free     = equity * 0.20
        if free_margin < min_free:
            self._notify(
                f"⛔ <b>Trade bloqueado — {symbol}</b>\n"
                f"Margem livre: <code>${free_margin:.2f}</code> — mínimo: <code>${min_free:.2f}</code>"
            )
            return {"status": "blocked", "reason": "Margem insuficiente"}

        size = self._risk.calculate_position_size(equity, entry, sl)
        if size <= 0:
            return {"status": "error", "reason": "Size zero"}

        # Ajusta size se margem insuficiente
        margin_needed = (size * entry) / leverage
        if margin_needed > free_margin * 0.9:
            size = self._hl.round_size(symbol, (free_margin * 0.9 * leverage) / entry)
            if size <= 0:
                return {"status": "blocked", "reason": "Size após ajuste = zero"}

        val = self._risk.validate_trade(signal, self._state.active_positions,
                                         self._state.daily_pnl, total_equity=equity)
        if not val["approved"]:
            return {"status": "blocked", "reason": val["reason"]}

        pos = {
            "direction": direction, "entry": entry, "stop_loss": sl, "tp1": tp1, "tp2": tp2,
            "size": size, "leverage": leverage, "equity_open": equity,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "peak_pnl": 0.0, "tp1_hit": False, "status": "open",
            "entry_order_id": None, "sl_order_id": None,
            "tp1_order_id": None, "tp2_order_id": None,
        }

        if config.DRY_RUN:
            self._state.active_positions[symbol] = pos
            self._state.stats["total"] += 1
            self._state.daily_trades_count += 1
        else:
            try:
                result = self._executor.open_position(symbol, direction, size, entry, leverage)

                if result["status"] == "rejected":
                    return result

                if result["status"] == "pending":
                    pos["status"]          = "pending_fill"
                    pos["entry_order_id"]  = result["order_id"]
                    self._state.active_positions[symbol] = pos
                    self._state.stats["total"] += 1
                    self._state.daily_trades_count += 1
                    self._notify(messages.pending_fill_message(
                        symbol, direction, result["exec_price"], size, result["order_id"]
                    ))
                    self._save_state()
                    return {"status": "pending_fill"}

                pos["entry"]          = result["real_entry"]
                pos["entry_order_id"] = result["order_id"]
                pos = self._executor.place_protection_orders(
                    symbol, pos, size, direction == "LONG", sl, tp1, tp2
                )
                pos["status"] = "open"
                self._state.active_positions[symbol] = pos
                self._state.stats["total"] += 1
                self._state.daily_trades_count += 1
                logger.info(
                    f"{direction} {symbol} @ ${pos['entry']:,.4f} "
                    f"SL={pos['sl_order_id']} TP1={pos['tp1_order_id']}"
                )
            except Exception as e:
                logger.error(f"Erro execução {symbol}: {e}", exc_info=True)
                self._notify(f"🚨 <b>ERRO execução {symbol}</b>\n<code>{str(e)[:200]}</code>")
                return {"status": "error", "error": str(e)}

        self._save_state()
        self._notify(messages.entry_message(
            symbol, direction, leverage, confidence,
            pos.get("entry", entry), size, equity,
            sl, tp1, tp2, round(rr, 2), signal, pos
        ))
        return {"status": "dry_run" if config.DRY_RUN else "executed"}

    def generate_daily_report(self) -> str:
        total = self._state.stats["total"]
        wr    = round(self._state.stats["wins"] / total * 100, 1) if total > 0 else 0
        return self._claude.ask(
            "Gere relatório executivo da sessão em português.",
            {
                "session_pnl": self._state.session_pnl,
                "total": total, "wr": f"{wr}%",
                "wins": self._state.stats["wins"],
                "losses": self._state.stats["losses"],
            },
            symbol="_report"
        )

    # ── Helpers internos

    async def _run_analysis_cycle(self) -> list:
        results = []
        for sym in config.SYMBOLS:
            if self.paused:
                break
            if sym in self._state.active_positions:
                pos    = self._state.active_positions[sym]
                cur    = self._hl.get_mark_price(sym)
                il     = pos["direction"] == "LONG"
                entry  = float(pos.get("entry", cur) or cur)
                pp     = ((cur - entry) / entry * 100) if il else ((entry - cur) / entry * 100)
                pl     = pp * pos["leverage"]
                em     = "🟢" if pl >= 0 else "🔴"
                st     = "⏳" if pos.get("status") == "pending_fill" else ""
                results.append(f"{em}{st} <b>{sym}</b> em posição | <code>{pl:+.2f}%</code>")
                continue

            if len(self._state.active_positions) >= config.MAX_POSITIONS:
                results.append(f"⏸ <b>{sym}</b> — aguardando vaga")
                continue

            r      = self.analyze_market(sym)
            signal = r.get("signal")
            sig    = signal.get("signal", "WAIT") if signal else "WAIT"
            conf   = signal.get("confidence", 0)  if signal else 0
            momento = signal.get("momento", "")   if signal else ""

            em = {"LONG": "🟢", "SHORT": "🔴"}.get(sig, "⚪")
            dp = {"LONG": "COMPRAR", "SHORT": "VENDER"}.get(sig, "ESPERAR")
            linha = f"{em} <b>{sym}</b> — {dp} <code>{conf}%</code>"
            if momento:
                linha += f"\n    <i>{str(momento)[:120]}</i>"
            results.append(linha)

            if sig in ("LONG", "SHORT") and conf >= config.MIN_CONFIDENCE:
                self.execute_trade(sym, signal)

            await asyncio.sleep(8)
        return results

    async def _wait_with_monitoring(self, total_seconds: int, pnl_tick: int):
        elapsed      = 0
        monitor_freq = min(config.MONITOR_FREQ, total_seconds)
        while elapsed < total_seconds:
            await asyncio.sleep(monitor_freq)
            elapsed  += monitor_freq
            if self.paused:
                break
            if self._state.active_positions:
                self._monitor.check_all(self._state.active_positions)
                pnl_tick += monitor_freq
                if pnl_tick >= config.PNL_UPDATE_INTERVAL:
                    self._notify_pnl_update()
                    pnl_tick = 0

    def _check_new_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self._current_day:
            old_pnl    = self._state.daily_pnl
            old_trades = self._state.daily_trades_count
            self._state.daily_pnl          = 0.0
            self._state.daily_trades_count = 0
            self._current_day              = today
            self._notify(
                f"🌅 <b>Novo dia</b> | "
                f"P&L ontem: <code>${old_pnl:+.2f}</code> | "
                f"Trades: <code>{old_trades}</code>"
            )

    def _check_daily_loss_limit(self):
        capital   = self._hl.get_account_info().get("equity", 0)
        daily_lim = capital * config.DAILY_LOSS_LIMIT
        if self._state.daily_pnl < 0 and daily_lim > 0 and abs(self._state.daily_pnl) >= daily_lim:
            self._notify(
                f"⛔ <b>STOP DIÁRIO</b> | "
                f"Perda: <code>${self._state.daily_pnl:.2f}</code> | "
                f"Limite: <code>${daily_lim:.2f}</code>\nPausado 2h."
            )
            raise _DailyLimitReached()

    def _get_leverage(self, symbol: str) -> int:
        configured   = config.LEVERAGE_MAP.get(symbol, config.LEVERAGE_FALLBACK)
        platform_max = self._leverage_limits.get(symbol, config.MAX_LEVERAGE)
        return min(configured, platform_max, config.MAX_LEVERAGE)

    def _load_leverage_limits(self):
        try:
            info = self._hl.get_asset_info()
            for sym in config.SYMBOLS:
                self._leverage_limits[sym] = info.get(sym, {}).get("maxLeverage", config.LEVERAGE_FALLBACK)
        except Exception as e:
            logger.warning(f"leverage limits: {e}")

    def _sync_with_exchange(self):
        try:
            logger.info("Sincronizando com a exchange...")
            acc           = self._hl.get_account_info()
            real_pos      = self._hl.get_all_positions()
            open_orders   = self._hl.get_open_orders()
            fills_summary = self._hl.get_fills_summary()
            funding       = self._hl.get_funding_summary()
            equity        = acc.get("equity", 0)

            for sym, rp in real_pos.items():
                if sym not in self._state.active_positions:
                    logger.warning(f"Posição real: {sym} {'LONG' if rp['is_long'] else 'SHORT'} size={rp['size']}")
                    self._state.active_positions[sym] = {
                        "direction":   "LONG" if rp["is_long"] else "SHORT",
                        "entry":       rp["entry_price"],
                        "stop_loss": 0, "tp1": 0, "tp2": 0,
                        "size":        rp["size"],
                        "leverage":    self._get_leverage(sym),
                        "equity_open": equity,
                        "opened_at":   datetime.now(timezone.utc).isoformat(),
                        "peak_pnl": 0.0, "tp1_hit": False, "status": "open",
                        "entry_order_id": None, "sl_order_id": None,
                        "tp1_order_id": None, "tp2_order_id": None,
                        "synced_from_exchange": True,
                        "unrealized_pnl": rp["unrealized_pnl"],
                        "liq_price":      rp["liq_price"],
                    }

            for sym in list(self._state.active_positions.keys()):
                if sym not in real_pos:
                    logger.warning(f"Posição {sym} no estado mas não na exchange — removendo")
                    del self._state.active_positions[sym]

            self._save_state()
            self._notify(messages.sync_message(equity, real_pos, open_orders, fills_summary, funding))
            logger.info(f"Sync OK: {len(real_pos)} posições, {len(open_orders)} ordens")
        except Exception as e:
            logger.error(f"Sync: {e}", exc_info=True)

    def _on_position_closed(self, symbol: str, pos: dict, exit_price: float,
                             reason: str, pnl_usd: float, fee: float, dur_min: int):
        """Callback chamado pelo PositionMonitor quando fecha uma posição."""
        self._state.session_pnl += pnl_usd
        self._state.daily_pnl   += pnl_usd
        if pnl_usd >= 0:
            self._state.stats["wins"]  += 1
            self._state.stats["best"]   = max(self._state.stats["best"], pnl_usd)
        else:
            self._state.stats["losses"] += 1
            self._state.stats["worst"]  = min(self._state.stats["worst"], pnl_usd)
        self._state.trade_history.append({
            **pos, "symbol": symbol, "exit": exit_price,
            "exit_reason": reason, "pnl_usd": pnl_usd,
            "fee": fee, "duration_min": dur_min,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })

    def _save_state(self):
        self._state_mgr.save(self._state)

    def _notify_pnl_update(self):
        if not self._state.active_positions:
            return
        real_pnls = {}
        if not config.DRY_RUN:
            try:
                for sym, rp in self._hl.get_all_positions().items():
                    real_pnls[sym] = rp.get("unrealized_pnl", 0.0)
            except Exception:
                pass
        self._notify(messages.pnl_update_message(
            self._state.active_positions, real_pnls,
            self._state.daily_pnl, self._state.stats
        ))

    def _notify(self, msg: str):
        if self._telegram and config.TELEGRAM_CHAT_ID:
            try:
                loop = asyncio.get_event_loop()
                if loop and loop.is_running():
                    asyncio.ensure_future(self._telegram.notify(config.TELEGRAM_CHAT_ID, msg))
                else:
                    loop.run_until_complete(self._telegram.notify(config.TELEGRAM_CHAT_ID, msg))
            except Exception as e:
                logger.error(f"Notify: {e}")

    @property
    def telegram_bot(self):
        return self._telegram

    @telegram_bot.setter
    def telegram_bot(self, value):
        self._telegram = value


class _DailyLimitReached(Exception):
    """Sinaliza que o limite diário foi atingido e o bot deve pausar."""
    pass
