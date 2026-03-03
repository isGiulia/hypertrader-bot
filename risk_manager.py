"""
Risk Manager
Gestao de capital profissional com crescimento composto e protecao de drawdown
"""

import logging

logger = logging.getLogger("RiskManager")


class RiskManager:

    def __init__(self, config: dict):
        self.config      = config
        self.peak_equity = 0.0   # pico historico de equity — persistido no state.json

    # ══════════════════════════════════════════════
    #  CAPITAL E DRAWDOWN
    # ══════════════════════════════════════════════

    def update_peak(self, equity: float):
        """Atualiza o pico historico de equity (chamado a cada ciclo)."""
        if equity > self.peak_equity:
            self.peak_equity = equity

    def get_drawdown_pct(self, equity: float) -> float:
        """Calcula o drawdown percentual atual em relacao ao pico."""
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - equity) / self.peak_equity * 100)

    def get_size_multiplier(self, equity: float) -> float:
        """
        Retorna o multiplicador de tamanho de posicao baseado no drawdown:
          0-10%  drawdown -> 1.0 (tamanho normal)
          10-20% drawdown -> 0.5 (modo defensivo)
          >20%   drawdown -> 0.0 (para de operar)
        """
        dd = self.get_drawdown_pct(equity)
        if dd >= self.config["drawdown_stop"]:
            return 0.0
        if dd >= self.config["drawdown_half"]:
            return 0.5
        return 1.0

    def get_trading_capital(self, equity: float) -> float:
        """Capital base para calcular risco (allocation * equity)."""
        return equity * self.config.get("trading_allocation", 1.0)

    def get_risk_metrics(self, equity: float, active_positions: dict) -> dict:
        """Retorna metricas de risco completas para exibicao no Telegram."""
        dd      = self.get_drawdown_pct(equity)
        mult    = self.get_size_multiplier(equity)
        capital = self.get_trading_capital(equity)

        if dd >= self.config["drawdown_stop"]:
            status = "🚨 PARADO — drawdown critico"
        elif dd >= self.config["drawdown_half"]:
            status = "⚠️ DEFENSIVO — posicoes reduzidas 50%"
        else:
            status = "✅ NORMAL"

        return {
            "equity":            equity,
            "peak_equity":       self.peak_equity,
            "drawdown_pct":      round(dd, 2),
            "drawdown_status":   status,
            "size_multiplier":   mult,
            "trading_capital":   capital,
            "open_positions":    len(active_positions),
            "max_positions":     self.config["max_positions"],
        }

    # ══════════════════════════════════════════════
    #  TAMANHO DE POSICAO — crescimento composto
    # ══════════════════════════════════════════════

    def calculate_position_size(self, equity: float, entry: float, stop_loss: float) -> float:
        """
        Calcula o tamanho de posicao baseado em:
        - 1% do equity total como risco maximo
        - Distancia do SL para saber quantos contratos comprar
        - Multiplicador de drawdown (0.5 em modo defensivo)

        Formula: size = (equity * risk_pct * mult) / (distancia_sl_pct)

        O tamanho cresce automaticamente conforme o equity cresce (composto).
        """
        if entry <= 0 or stop_loss <= 0:
            logger.warning("Entrada ou SL invalidos para calculo de tamanho")
            return 0

        sl_dist_pct = abs(entry - stop_loss) / entry
        if sl_dist_pct <= 0:
            logger.warning("Distancia de SL zero — impossivel calcular tamanho")
            return 0

        mult     = self.get_size_multiplier(equity)
        capital  = self.get_trading_capital(equity)
        risk_usd = capital * self.config["risk_per_trade"] * mult

        # Tamanho em contratos (USDC de margem / preco de entrada)
        size = risk_usd / (sl_dist_pct * entry)

        # Arredonda para 4 casas decimais (precisao Hyperliquid)
        size = round(size, 4)

        logger.debug(
            f"Tamanho calculado: equity=${equity:.2f} risk=${risk_usd:.2f} "
            f"sl_dist={sl_dist_pct*100:.2f}% size={size}"
        )
        return max(size, 0)

    # ══════════════════════════════════════════════
    #  VALIDACAO DE TRADE
    # ══════════════════════════════════════════════

    def validate_trade(self, signal: dict, active_positions: dict,
                       daily_pnl: float, total_equity: float) -> dict:
        """
        Valida se e seguro abrir um novo trade.
        Retorna {"approved": True/False, "reason": str}
        """
        direction = signal.get("signal", "WAIT")
        symbol    = signal.get("symbol", "?")

        # 1. Drawdown critico — para completamente
        mult = self.get_size_multiplier(total_equity)
        if mult == 0.0:
            dd = self.get_drawdown_pct(total_equity)
            return {
                "approved": False,
                "reason":   f"Drawdown critico {dd:.1f}% >= {self.config['drawdown_stop']}%. Bot pausado ate recuperacao."
            }

        # 2. Maximo de posicoes
        if len(active_positions) >= self.config["max_positions"]:
            return {
                "approved": False,
                "reason":   f"Maximo de {self.config['max_positions']} posicoes atingido."
            }

        # 3. Correlacao — nao abre mesmo simbolo duas vezes
        if symbol in active_positions:
            return {
                "approved": False,
                "reason":   f"{symbol} ja tem posicao aberta."
            }

        # 4. Stop diario — se perdeu demais hoje
        daily_limit_usd = self.get_trading_capital(total_equity) * self.config["daily_loss_limit"]
        if daily_pnl < 0 and daily_limit_usd > 0 and abs(daily_pnl) >= daily_limit_usd:
            return {
                "approved": False,
                "reason":   f"Stop diario atingido: ${daily_pnl:.2f} >= limite ${daily_limit_usd:.2f}."
            }

        # 5. Confianca minima
        conf = signal.get("confidence", 0)
        if conf < self.config.get("min_confidence", 75):
            return {
                "approved": False,
                "reason":   f"Confianca {conf}% abaixo do minimo {self.config['min_confidence']}%."
            }

        # 6. R:R minimo
        entry = float(signal.get("entry_price", 0))
        sl    = float(signal.get("stop_loss", 0))
        tp1   = float(signal.get("take_profit_1", 0))
        if entry > 0 and sl > 0 and tp1 > 0:
            risk   = abs(entry - sl)
            reward = abs(tp1 - entry)
            rr     = reward / risk if risk > 0 else 0
            min_rr = self.config.get("take_profit_ratio", 2.0)
            if rr < min_rr:
                return {
                    "approved": False,
                    "reason":   f"R:R {rr:.2f} abaixo do minimo {min_rr}."
                }

        return {"approved": True, "reason": "OK"}

    # ══════════════════════════════════════════════
    #  TRAILING STOP
    # ══════════════════════════════════════════════

    def calculate_trailing_stop(self, current_price: float, direction: str, atr: float) -> float:
        """
        Calcula novo trailing stop baseado em ATR (1.5x ATR).
        Sempre move o stop na direcao favoravel — nunca contra.
        """
        trail_dist = atr * 1.5
        if direction == "LONG":
            return round(current_price - trail_dist, 6)
        else:
            return round(current_price + trail_dist, 6)
