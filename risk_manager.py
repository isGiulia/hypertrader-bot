"""
Risk Manager — Gestao Profissional de Capital
=============================================

Filosofia:

  REGRA 1 — Nunca exponha tudo:
    Apenas 20% do capital total e alocado para trading ativo.
    Os outros 80% ficam protegidos independente do que aconteca.
    Com $1.000     -> $200 em trading, $800 protegidos
    Com $1.000.000 -> $200.000 em trading, $800.000 protegidos

  REGRA 2 — Crescimento composto automatico:
    Risco calculado sobre equity ATUAL, nao valor fixo.
    Lucrou $100 -> capital cresce -> proxima posicao e maior automaticamente.
    Perdeu $50  -> capital encolhe -> proxima posicao e menor automaticamente.
    Nunca precisa reconfigurar conforme o capital muda.

  REGRA 3 — Drawdown protection:
    Caiu 10% do pico historico -> posicoes reduzidas 50% (modo defensivo)
    Caiu 20% do pico historico -> bot para completamente (capital em risco)
    Quando equity se recuperar, volta ao normal automaticamente.

  REGRA 4 — Matematica consistente:
    max_trades_day=3, risk_per_trade=1% -> perda max por dia = 3%
    daily_loss_limit=3% -> stop diario nunca contradiz o risco por trade
    Tudo alinhado, sem inconsistencias.
"""

import logging

logger = logging.getLogger("RiskManager")


class RiskManager:

    def __init__(self, config: dict):
        self.config      = config
        self.peak_equity = 0.0  # pico historico para calculo de drawdown

    # ── Capital alocado ───────────────────────────────────────────────────────

    def get_trading_capital(self, total_equity: float) -> float:
        """
        Retorna apenas a fracao do capital disponivel para trading ativo.
        Padrao: 20% do equity total — nunca coloca tudo para trabalhar.
        """
        allocation = self.config.get("trading_allocation", 0.20)
        return total_equity * allocation

    # ── Drawdown protection ───────────────────────────────────────────────────

    def update_peak(self, equity: float):
        """Atualiza pico historico — chamado a cada ciclo"""
        if equity > self.peak_equity:
            self.peak_equity = equity

    def get_drawdown_pct(self, equity: float) -> float:
        """Drawdown atual em % relativo ao pico historico"""
        if self.peak_equity == 0:
            self.peak_equity = equity
            return 0.0
        return max(0.0, (self.peak_equity - equity) / self.peak_equity * 100)

    def get_size_multiplier(self, equity: float) -> float:
        """
        Multiplicador do tamanho das posicoes baseado no drawdown:
          0-10%  -> tamanho NORMAL    (1.0x)
          10-20% -> tamanho REDUZIDO  (0.5x)
          >20%   -> STOP COMPLETO     (0.0x)
        """
        dd       = self.get_drawdown_pct(equity)
        stop_dd  = self.config.get("drawdown_stop", 20.0)
        half_dd  = self.config.get("drawdown_half", 10.0)
        if dd >= stop_dd:
            return 0.0
        elif dd >= half_dd:
            return 0.5
        return 1.0

    # ── Tamanho da posicao — crescimento composto ─────────────────────────────

    def calculate_position_size(self, total_equity: float, entry: float,
                                stop_loss: float, risk_pct: float = None) -> float:
        """
        Calcula tamanho de posicao de forma profissional e composta:

          1. Capital de trading = total_equity x 20%
          2. Aplica reducao de drawdown se necessario
          3. Risco em $ = capital_trading x risk_per_trade (1%)
          4. Tamanho = risco_$ / distancia_percentual_do_SL

        Crescimento automatico:
          Hoje $1.000  -> trading=$200 -> risco=$2 -> posicao pequena
          Lucro $1.200 -> trading=$240 -> risco=$2.40 -> posicao maior
          Perda $900   -> trading=$180 -> risco=$1.80 -> posicao menor
        """
        if risk_pct is None:
            risk_pct = self.config.get("risk_per_trade", 0.01)

        if entry == 0 or stop_loss == 0 or total_equity == 0:
            return 0

        self.update_peak(total_equity)
        trading_capital = self.get_trading_capital(total_equity)
        multiplier      = self.get_size_multiplier(total_equity)
        effective_cap   = trading_capital * multiplier

        if effective_cap == 0:
            return 0

        risk_amount    = effective_cap * risk_pct
        price_risk_pct = abs(entry - stop_loss) / entry

        if price_risk_pct == 0:
            return 0

        position_value = risk_amount / price_risk_pct
        position_size  = position_value / entry

        max_lev  = self.config.get("max_leverage", 10)
        max_size = (effective_cap * max_lev) / entry
        final    = min(position_size, max_size)

        logger.info(
            f"PositionSize: {final:.6f} | "
            f"TotalEq: ${total_equity:.0f} | "
            f"TradingCap: ${trading_capital:.0f} | "
            f"EffCap: ${effective_cap:.0f} | "
            f"Risk$: ${risk_amount:.2f} | "
            f"SL%: {price_risk_pct*100:.2f}% | "
            f"DDmult: {multiplier:.1f}x"
        )
        return round(final, 6)

    # ── Validacao do trade ────────────────────────────────────────────────────

    def validate_trade(self, signal: dict, active_positions: dict,
                       daily_pnl: float, total_equity: float = 0) -> dict:
        """Valida se o trade pode ser executado"""

        # 1. Maximo de posicoes
        if len(active_positions) >= self.config["max_positions"]:
            return {"approved": False,
                    "reason": f"Maximo de posicoes ({self.config['max_positions']}) atingido"}

        # 2. Drawdown protection
        if total_equity > 0:
            self.update_peak(total_equity)
            mult   = self.get_size_multiplier(total_equity)
            dd_pct = self.get_drawdown_pct(total_equity)
            if mult == 0.0:
                stop = self.config.get("drawdown_stop", 20)
                return {
                    "approved": False,
                    "reason":   f"DRAWDOWN STOP: conta caiu {dd_pct:.1f}% do pico "
                                f"(limite: {stop}%). Bot protegendo capital."
                }

        # 3. Stop diario — consistente com risco por trade
        if total_equity > 0:
            trading_cap  = self.get_trading_capital(total_equity)
            daily_limit  = trading_cap * self.config.get("daily_loss_limit", 0.03)
            if daily_pnl < 0 and daily_limit > 0 and abs(daily_pnl) >= daily_limit:
                return {
                    "approved": False,
                    "reason":   f"Stop diario: -${abs(daily_pnl):.2f} "
                                f"(limite ${daily_limit:.2f} = "
                                f"{self.config.get('daily_loss_limit',0.03)*100:.0f}% "
                                f"do capital alocado)"
                }

        # 4. R:R minimo 1:2
        if not self._validate_rr(signal):
            return {"approved": False, "reason": "R:R insuficiente (minimo 1:2)"}

        # 5. Correlacao
        symbol    = signal.get("symbol", "")
        direction = signal.get("signal", "")
        if self._check_correlation(symbol, direction, active_positions):
            return {"approved": False,
                    "reason": "Correlacao alta — par similar ja aberto na mesma direcao"}

        return {"approved": True, "reason": "Trade aprovado"}

    def _validate_rr(self, signal: dict) -> bool:
        try:
            entry  = float(signal.get("entry_price", 0))
            sl     = float(signal.get("stop_loss", 0))
            tp1    = float(signal.get("take_profit_1", 0))
            if not all([entry, sl, tp1]):
                return False
            risk   = abs(entry - sl)
            reward = abs(tp1 - entry)
            return risk > 0 and (reward / risk) >= self.config.get("take_profit_ratio", 2.0)
        except Exception:
            return False

    def _check_correlation(self, symbol: str, direction: str, active: dict) -> bool:
        groups = {
            "btc_eth":  ["BTC", "ETH"],
            "sol_avax": ["SOL", "AVAX", "APT", "SUI"],
            "defi":     ["ARB", "OP", "INJ"],
            "meme":     ["PEPE", "DOGE", "SHIB", "WIF"],
        }
        for assets in groups.values():
            if symbol in assets:
                for sym, pos in active.items():
                    if sym in assets and sym != symbol:
                        if pos.get("direction") == direction:
                            return True
        return False

    # ── Trailing stop ─────────────────────────────────────────────────────────

    def calculate_trailing_stop(self, current_price: float, direction: str,
                                atr: float) -> float:
        """Trailing stop dinamico 2x ATR"""
        mult = 2.0
        if direction == "LONG":
            return current_price - (atr * mult)
        return current_price + (atr * mult)

    # ── Metricas completas ────────────────────────────────────────────────────

    def get_risk_metrics(self, total_equity: float, positions: dict) -> dict:
        """Snapshot completo da saude financeira do bot"""
        self.update_peak(total_equity)
        trading_cap    = self.get_trading_capital(total_equity)
        dd_pct         = self.get_drawdown_pct(total_equity)
        mult           = self.get_size_multiplier(total_equity)
        total_exposure = sum(p.get("size", 0) * p.get("entry", 0) for p in positions.values())
        exposure_pct   = (total_exposure / total_equity * 100) if total_equity > 0 else 0

        return {
            "total_equity":      round(total_equity, 2),
            "trading_capital":   round(trading_cap, 2),
            "protected_capital": round(total_equity - trading_cap, 2),
            "peak_equity":       round(self.peak_equity, 2),
            "drawdown_pct":      round(dd_pct, 2),
            "drawdown_status":   "STOP"     if mult == 0.0 else
                                 "REDUZIDO" if mult == 0.5 else
                                 "NORMAL",
            "size_multiplier":   mult,
            "total_exposure":    round(total_exposure, 2),
            "exposure_pct":      round(exposure_pct, 2),
            "positions_count":   len(positions),
            "risk_level":        "CRITICO" if dd_pct >= 20 else
                                 "ALTO"    if dd_pct >= 10 else
                                 "MEDIO"   if exposure_pct > 25 else
                                 "BAIXO",
        }
