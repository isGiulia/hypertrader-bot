"""
agent.py — HyperTraderAgent: orquestrador central do bot.

Princípio Clean Code: "Um orquestrador não deve ter lógica — ele delega."
Esta classe é o "maestro": coordena os módulos mas não implementa nada.
Cada responsabilidade real fica no módulo especializado.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
try:
    _DISPLAY_TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    _DISPLAY_TZ = timezone.utc

from hypertrader import config
from hypertrader.ai.claude_client import ClaudeClient
from hypertrader.core.state_manager import StateManager, TradingState
from hypertrader.core.journal import TradeJournal
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
        self._risk = RiskManager({
            "drawdown_stop":      config.DRAWDOWN_STOP,
            "drawdown_half":      config.DRAWDOWN_HALF,
            "trading_allocation": 1.0,
            "risk_per_trade":     config.RISK_PER_TRADE,
            "max_positions":      config.MAX_POSITIONS,
            "daily_loss_limit":   config.DAILY_LOSS_LIMIT,
            "min_confidence":     config.MIN_CONFIDENCE,
            "take_profit_ratio":  config.TAKE_PROFIT_RATIO,
        })

        # ── Estado
        self._state        = self._state_mgr.load()
        self._current_day  = datetime.now(timezone.utc).date()
        self._telegram     = None
        self._last_reconcile_ts = 0.0

        # ── Loop principal (setado em run_autonomous) — usado pelo _notify
        self._main_loop    = None

        # Journal persistente (append-only) — histórico "para sempre"
        self._journal = TradeJournal(config.JOURNAL_FILE)
        self._journal.log("bot_start", {
            "testnet": config.USE_TESTNET,
            "dry_run": config.DRY_RUN,
            "symbols": config.SYMBOLS,
        })

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

        # ── Reaplica métricas persistidas / overrides
        # peak_equity precisa sobreviver reinícios para drawdown funcionar.
        self._risk.peak_equity = float(self._state.peak_equity or 0.0)
        self._apply_runtime_overrides()

        logger.info(
            f"HyperTrader iniciado | {config.SYMBOLS} | "
            f"{'TESTNET' if config.USE_TESTNET else 'MAINNET'} | "
            f"{'DRY RUN' if config.DRY_RUN else 'LIVE'}"
        )

    def _apply_runtime_overrides(self):
        """Aplica overrides persistidos do state para o risk_manager."""
        try:
            mult = float(self._state.risk_multiplier or 1.0)
            mult = max(0.1, min(mult, 1.0))
            self._state.risk_multiplier = mult

            base_risk = float(config.RISK_PER_TRADE)
            self._risk.config["risk_per_trade"] = base_risk * mult

            min_conf = int(config.MIN_CONFIDENCE)
            if int(self._state.min_confidence_override or 0) > 0:
                min_conf = max(min_conf, int(self._state.min_confidence_override))
            self._risk.config["min_confidence"] = min_conf
        except Exception:
            pass

    def _is_time_paused(self) -> bool:
        return bool(self._state.pause_until_ts and time.time() < self._state.pause_until_ts)

    def _pause_for(self, hours: float, reason: str):
        until = time.time() + max(0.0, hours) * 3600.0
        self._pause_until(until, reason)

    def _pause_until(self, until_ts: float, reason: str):
        self._state.pause_until_ts = float(until_ts)
        self._state.pause_reason = str(reason or "")
        self._save_state()
        self._journal.log("risk_pause", {"until_ts": self._state.pause_until_ts, "reason": self._state.pause_reason})
        self._notify(
            f"⏸ <b>PAUSA AUTOMÁTICA</b>\n"
            f"Motivo: <code>{self._state.pause_reason}</code>\n"
            f"Até: <code>{datetime.fromtimestamp(self._state.pause_until_ts, tz=_DISPLAY_TZ).strftime('%Y-%m-%d %H:%M %Z')}</code>"
        )

    def _maybe_auto_resume(self):
        if self._state.pause_until_ts and time.time() >= self._state.pause_until_ts:
            reason = self._state.pause_reason
            self._state.pause_until_ts = 0.0
            self._state.pause_reason = ""
            self._save_state()
            self._journal.log("risk_resume", {"reason": reason})
            self._notify(f"▶️ <b>Retomando automaticamente</b>\nMotivo anterior: <code>{reason}</code>")

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
        # Guarda referência ao loop principal para uso do _notify (thread-safe)
        self._main_loop = asyncio.get_running_loop()
        logger.info("Loop autônomo iniciado")
        self._notify(messages.startup_message())

        # Snapshot inicial (capital + posições reais) — assim o usuário sempre sabe onde está
        self._notify(self._account_snapshot_message())

        while True:
            try:
                self._maybe_auto_resume()

                # Mesmo pausado: monitor continua para não "perder" fechamento/TP/SL
                if self.paused or self._is_time_paused():
                    if self._state.active_positions and not config.DRY_RUN:
                        self._monitor.check_all(self._state.active_positions)
                    await asyncio.sleep(max(5, config.MONITOR_FREQ))
                    continue

                self._check_new_day()
                self._check_daily_loss_limit()

                # Reconcile silencioso (evita estado "perdido" se algo mudar na exchange)
                self._reconcile_silent(interval_seconds=600)

                # Verifica se posições foram fechadas na exchange (SL/TP executados)
                self._monitor.check_all(self._state.active_positions)

                acc_val = self._hl.get_account_info().get("equity", 0)
                self._risk.update_peak(acc_val)
                # persiste peak_equity pra drawdown sobreviver restart
                self._state.peak_equity = float(self._risk.peak_equity or acc_val or 0.0)

                # Meta de patrimônio (opcional)
                if config.TARGET_EQUITY and acc_val >= config.TARGET_EQUITY:
                    today_utc = datetime.now(timezone.utc).date().isoformat()
                    if self._state.last_target_hit_date != today_utc:
                        self._state.last_target_hit_date = today_utc
                        self._save_state()
                        self._journal.log("target_hit", {"equity": acc_val, "target": config.TARGET_EQUITY})
                        self._notify(
                            f"🎯 <b>Meta atingida</b>\n"
                            f"Equity: <code>${acc_val:,.2f}</code> >= alvo <code>${config.TARGET_EQUITY:,.2f}</code>\n"
                            f"Ação: <code>{config.ON_TARGET_ACTION}</code>"
                        )
                    if config.ON_TARGET_ACTION == "pause":
                        # Pausa novas entradas até você decidir
                        self._pause_for(24, "Meta atingida — proteção")

                metrics = self._risk.get_risk_metrics(acc_val, self._state.active_positions)

                # Persiste ajustes/peak a cada ciclo (baixa frequência)
                self._save_state()

                results = await self._run_analysis_cycle()

                next_ts  = datetime.now(timezone.utc).timestamp() + config.ANALYSIS_INTERVAL
                next_str = datetime.fromtimestamp(next_ts, tz=_DISPLAY_TZ).strftime("%H:%M %Z")

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

                # Aguarda próximo ciclo verificando fechamentos a cada 30s (sem spam)
                await self._wait_with_monitoring(config.ANALYSIS_INTERVAL)

            except Exception as e:
                logger.error(f"Erro loop: {e}", exc_info=True)
                self._notify(f"🚨 <b>Erro loop</b>\n<code>{str(e)[:200]}</code>")
                self._journal.log("error", {"where": "run_autonomous", "err": str(e)[:500]})
                await asyncio.sleep(30)

    def _reconcile_silent(self, interval_seconds: int = 600):
        """Reconcilia posições reais vs estado local sem spam.

        - Se detectar posição real que o bot não conhece, adiciona no estado.
        - Se detectar que o bot tem posição mas a exchange não, o monitor cuidará.
        """
        if config.DRY_RUN:
            return
        now = time.time()
        if (now - self._last_reconcile_ts) < interval_seconds:
            return
        self._last_reconcile_ts = now
        try:
            acc = self._hl.get_account_info()
            equity = float(acc.get("equity", 0) or 0)
            real_pos = self._hl.get_all_positions()
            added = 0
            for sym, rp in real_pos.items():
                if sym not in self._state.active_positions:
                    self._state.active_positions[sym] = {
                        "direction":   "LONG" if rp["is_long"] else "SHORT",
                        "entry":       rp["entry_price"],
                        "stop_loss": 0, "tp1": 0, "tp2": 0,
                        "size":        rp["size"],
                        "leverage":    int(rp.get("leverage", self._get_leverage(sym)) or self._get_leverage(sym)),
                        "equity_open": equity,
                        "opened_at":   datetime.now(timezone.utc).isoformat(),
                        "peak_pnl": 0.0, "tp1_hit": False, "status": "open",
                        "entry_order_id": None, "sl_order_id": None,
                        "tp1_order_id": None, "tp2_order_id": None,
                        "synced_from_exchange": True,
                        "unrealized_pnl": rp.get("unrealized_pnl", 0),
                        "liq_price": rp.get("liq_price", 0),
                        "risk_per_trade_used": float(self._risk.config.get("risk_per_trade", config.RISK_PER_TRADE)),
                        "min_confidence_used": int(self._risk.config.get("min_confidence", config.MIN_CONFIDENCE)),
                    }
                    added += 1
                    self._journal.log("reconcile_added", {"symbol": sym, "direction": self._state.active_positions[sym]["direction"]})

            if added > 0:
                self._save_state()
                self._notify(f"🔄 <b>Reconcile</b>: detectei e importei <code>{added}</code> posição(ões) abertas na exchange.")
        except Exception as e:
            self._journal.log("error", {"where": "reconcile", "err": str(e)[:500]})

    def _account_snapshot_message(self) -> str:
        """Mensagem rápida de snapshot (capital + posições + margem)."""
        try:
            acc = self._hl.get_account_info()
            equity = float(acc.get("equity", 0) or 0)
            margin_used = float(acc.get("margin_used", 0) or 0)
            free = float(acc.get("free_margin", max(equity - margin_used, 0)) or 0)
            real_pos = self._hl.get_all_positions() if not config.DRY_RUN else {}
            return messages.account_snapshot_message(
                equity=equity,
                margin_used=margin_used,
                free_margin=free,
                positions=real_pos,
                paused=(self.paused or self._is_time_paused()),
                daily_pnl=self._state.daily_pnl,
                session_pnl=self._state.session_pnl,
                daily_trades=self._state.daily_trades_count,
                max_trades=config.MAX_TRADES_DAY,
            )
        except Exception as e:
            return f"⚠️ Snapshot indisponível: <code>{str(e)[:150]}</code>"

    def close_position_manual(self, symbol: str, reason: str = "Manual") -> dict:
        sym = symbol.upper()
        if sym not in self._state.active_positions:
            return {"status": "not_found"}
        pos = self._state.active_positions[sym]
        cur = self._hl.get_mark_price(sym)
        try:
            # Reutiliza monitor para fechamento consistente + histórico
            self._monitor._close_and_remove(sym, pos, cur, reason, self._state.active_positions)
            self._save_state()
            self._journal.log("manual_close", {"symbol": sym, "reason": reason})
            return {"status": "closed"}
        except Exception as e:
            self._journal.log("error", {"where": "close_position_manual", "err": str(e)[:500]})
            return {"status": "error", "error": str(e)}

    # ── Análise e execução

    def analyze_market(self, symbol: str) -> dict:
        logger.info(f"Analisando {symbol}...")
        # V1: 4h/1h por padrão (mais estável e barato). V2 pode usar get_full_analysis.
        if getattr(config, "TRADING_STYLE", "4h_1h") in ("4h_1h", "4h/1h", "swing_4h_1h"):
            data = self._analyzer.get_v1_analysis(symbol)
        else:
            data = self._analyzer.get_full_analysis(symbol)

        leverage = self._get_leverage(symbol)

        # ═══════════════════════════════════════════════════════════════
        #  DEBUG TEMPORÁRIO — salva o payload enviado ao Claude
        #  REMOVER DEPOIS DO DIAGNÓSTICO
        # ═══════════════════════════════════════════════════════════════
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            with open(f"logs/payload_{symbol}.json", "w", encoding="utf-8") as _f:
                json.dump(data, _f, indent=2, ensure_ascii=False, default=str)
            logger.info(f"[DEBUG] payload_{symbol}.json salvo ({len(str(data))} chars)")
        except Exception as _e:
            logger.warning(f"[DEBUG] falha ao salvar payload: {_e}")
        # ═══════════════════════════════════════════════════════════════

        analysis = self._claude.ask(
            (
                f"{symbol}/USDC perp Hyperliquid. Estilo=4h/1h (4h tendência, 1h gatilho). "
                f"Alavancagem alvo={leverage}x (não inclua leverage no JSON). "
                f"Retorne APENAS o JSON válido."
            ),
            data, symbol=symbol
        )

        # ═══════════════════════════════════════════════════════════════
        #  DEBUG TEMPORÁRIO — salva a resposta crua do Claude
        #  REMOVER DEPOIS DO DIAGNÓSTICO
        # ═══════════════════════════════════════════════════════════════
        try:
            with open(f"logs/resposta_{symbol}.txt", "w", encoding="utf-8") as _f:
                _f.write(analysis or "")
            logger.info(f"[DEBUG] resposta_{symbol}.txt salva ({len(analysis or '')} chars)")
            logger.info(f"[DEBUG] {symbol} resposta (primeiros 800 chars): {str(analysis)[:800]}")
        except Exception as _e:
            logger.warning(f"[DEBUG] falha ao salvar resposta: {_e}")
        # ═══════════════════════════════════════════════════════════════

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

        free_margin = acc.get("free_margin", equity)

        size = self._risk.calculate_position_size(equity, entry, sl)
        if size <= 0:
            return {"status": "error", "reason": "Size zero"}

        # Ajuste profissional: se a posição calculada não cabe na margem, tente aumentar
        # a alavancagem (até o cap do V1) antes de reduzir o size.
        # Isso mantém o risco por trade (baseado no SL) sem forçar um size inválido.
        try:
            platform_max = int(self._leverage_limits.get(symbol, 0) or self._hl.get_max_leverage(symbol) or leverage)
            hard_cap = max(1, min(platform_max, int(getattr(config, "MAX_LEVERAGE", 20) or 20)))
            mode = (getattr(config, "LEVERAGE_MODE", "auto") or "auto").lower()
            cap = hard_cap if mode == "max" else max(1, min(hard_cap, int(getattr(config, "LEVERAGE_CAP", 5) or 5)))

            # Só tenta subir se houver espaço (em auto). Em max, já vem no hard_cap.
            if mode != "max" and leverage < cap and free_margin > 0:
                while leverage < cap:
                    margin_needed_tmp = (size * entry) / leverage
                    if margin_needed_tmp <= free_margin * 0.95:
                        break
                    leverage += 1
        except Exception:
            pass

        # Se margem livre insuficiente, ajusta o tamanho para caber
        margin_needed = (size * entry) / leverage
        if free_margin > 0 and margin_needed > free_margin * 0.95:
            size = self._hl.round_size(symbol, (free_margin * 0.95 * leverage) / entry)
            logger.info(f"Size ajustado por margem: ${free_margin:.2f} livre → size={size}")
            if size <= 0:
                self._notify(f"⛔ <b>{symbol} bloqueado</b> — Margem insuficiente (<code>${free_margin:.2f}</code>)")
                return {"status": "blocked", "reason": "Margem insuficiente"}

        val = self._risk.validate_trade(signal, self._state.active_positions,
                                         self._state.daily_pnl, total_equity=equity)
        if not val["approved"]:
            return {"status": "blocked", "reason": val["reason"]}

        pos = {
            "direction": direction, "entry": entry, "stop_loss": sl, "tp1": tp1, "tp2": tp2,
            "size": size, "leverage": leverage, "equity_open": equity,
            "risk_per_trade_used": float(self._risk.config.get("risk_per_trade", config.RISK_PER_TRADE)),
            "min_confidence_used": int(self._risk.config.get("min_confidence", config.MIN_CONFIDENCE)),
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
        try:
            self._journal.log("trade_open", {
                "symbol": symbol,
                "direction": direction,
                "entry": pos.get("entry", entry),
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "size": size,
                "leverage": leverage,
                "equity": equity,
                "confidence": confidence,
            })
        except Exception:
            pass
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
            "Gere um relatório executivo da sessão em português. "
            "Responda em texto corrido, sem JSON, sem backticks, sem markdown. "
            "Seja direto e objetivo como um trader profissional.",
            {
                "session_pnl": self._state.session_pnl,
                "total": total, "wr": f"{wr}%",
                "wins": self._state.stats["wins"],
                "losses": self._state.stats["losses"],
                "best_trade": self._state.stats.get("best", 0),
                "worst_trade": self._state.stats.get("worst", 0),
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

    async def _wait_with_monitoring(self, total_seconds: int):
        """
        Aguarda o próximo ciclo de análise checando fechamentos a cada 30s.
        NÃO envia spam de PNL — update só via /pnl no Telegram.
        """
        elapsed      = 0
        monitor_freq = min(config.MONITOR_FREQ, total_seconds)
        while elapsed < total_seconds:
            await asyncio.sleep(monitor_freq)
            elapsed += monitor_freq
            if self.paused:
                break
            if self._state.active_positions and not config.DRY_RUN:
                # Só verifica se posições foram fechadas na exchange (SL/TP/manual)
                self._monitor.check_all(self._state.active_positions)

    def _check_new_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self._current_day:
            old_pnl    = self._state.daily_pnl
            old_trades = self._state.daily_trades_count
            self._state.daily_pnl          = 0.0
            self._state.daily_trades_count = 0

            # reseta disciplina/performance do dia
            self._state.consecutive_wins = 0
            self._state.consecutive_losses = 0
            self._state.risk_multiplier = 1.0
            self._state.min_confidence_override = 0
            self._apply_runtime_overrides()

            self._current_day              = today
            self._notify(
                f"🌅 <b>Novo dia</b> | "
                f"P&L ontem: <code>${old_pnl:+.2f}</code> | "
                f"Trades: <code>{old_trades}</code>"
            )
            self._journal.log("new_day", {"prev_daily_pnl": old_pnl, "prev_trades": old_trades})

    def _check_daily_loss_limit(self):
        capital   = self._hl.get_account_info().get("equity", 0)
        daily_lim = capital * config.DAILY_LOSS_LIMIT
        if self._state.daily_pnl < 0 and daily_lim > 0 and abs(self._state.daily_pnl) >= daily_lim:
            self._pause_for(2, f"STOP DIÁRIO: perda ${self._state.daily_pnl:.2f} >= limite ${daily_lim:.2f}")

    def _get_leverage(self, symbol: str) -> int:
        sym = symbol.upper()
        platform_max = int(self._leverage_limits.get(sym, 0) or 0)
        if platform_max <= 0:
            platform_max = int(self._hl.get_max_leverage(sym) or config.LEVERAGE_FALLBACK)

        hard_cap = max(1, min(platform_max, int(getattr(config, "MAX_LEVERAGE", 20) or 20)))

        mode = (getattr(config, "LEVERAGE_MODE", "auto") or "auto").lower()
        if mode == "max":
            # Tenta usar o máximo permitido (ainda respeitando hard_cap).
            return hard_cap

        preferred = int(getattr(config, "LEVERAGE_MAP", {}).get(sym, getattr(config, "LEVERAGE_FALLBACK", 3)) or 3)
        cap = int(getattr(config, "LEVERAGE_CAP", 5) or 5)
        return max(1, min(preferred, hard_cap, cap))

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
            self._journal.log("sync", {
                "equity": equity,
                "positions": len(real_pos),
                "open_orders": len(open_orders),
                "fills_total": fills_summary.get("total_trades"),
                "funding_24h": funding.get("total_funding_usd"),
            })
            logger.info(f"Sync OK: {len(real_pos)} posições, {len(open_orders)} ordens")
        except Exception as e:
            logger.error(f"Sync: {e}", exc_info=True)
            self._journal.log("error", {"where": "sync", "err": str(e)[:500]})

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

        # ── Performance streak (auto-avaliação simples)
        if pnl_usd >= 0:
            self._state.consecutive_wins += 1
            self._state.consecutive_losses = 0
        else:
            self._state.consecutive_losses += 1
            self._state.consecutive_wins = 0

        self._journal.log("trade_close", {
            "symbol": symbol,
            "direction": pos.get("direction"),
            "pnl_usd": pnl_usd,
            "fee": fee,
            "reason": reason,
            "duration_min": dur_min,
        })

        # ── Regras de disciplina
        # 1) Se ganhou no primeiro trade do dia, pausa novas entradas até o próximo dia.
        if config.STOP_AFTER_FIRST_WIN and pnl_usd > 0 and self._state.daily_trades_count >= 1:
            # pausa até amanhã 00:05 no fuso de exibição
            now_local = datetime.now(_DISPLAY_TZ)
            tomorrow = (now_local.date().toordinal() + 1)
            next_day = datetime.fromordinal(tomorrow).replace(hour=0, minute=5, second=0, microsecond=0, tzinfo=_DISPLAY_TZ)
            self._pause_until(next_day.timestamp(), "Meta do dia: trade positivo atingido")

        # 2) Muitas perdas seguidas: reduz risco, aumenta rigor e pausa para "revisar".
        if self._state.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            self._state.risk_multiplier = max(0.1, float(self._state.risk_multiplier) * float(config.LOSS_RISK_MULTIPLIER))
            self._state.min_confidence_override = max(int(self._risk.config.get("min_confidence", config.MIN_CONFIDENCE)), int(config.MIN_CONFIDENCE) + int(config.LOSS_CONFIDENCE_ADD))
            self._apply_runtime_overrides()
            self._pause_for(float(config.LOSS_PAUSE_HOURS), f"{self._state.consecutive_losses} perdas seguidas — reduzindo risco e aumentando filtro")

        self._save_state()

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
        """
        Envia mensagem no Telegram de forma thread-safe.

        O bot pode chamar _notify de dentro do loop asyncio (thread principal)
        ou de threads secundárias (ex: TradeExecutor). Usamos
        run_coroutine_threadsafe para agendar no loop principal sem depender
        de event loop na thread atual.
        """
        if not (self._telegram and config.TELEGRAM_CHAT_ID):
            return
        try:
            loop = self._main_loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._telegram.notify(config.TELEGRAM_CHAT_ID, msg),
                    loop,
                )
            else:
                logger.warning(f"Notify sem loop ativo: {msg[:80]}")
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