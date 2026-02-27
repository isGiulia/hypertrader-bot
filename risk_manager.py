"""
Risk Manager
Gerenciamento de risco para trading de derivativos
"""

import logging

logger = logging.getLogger("RiskManager")


class RiskManager:

    def __init__(self, config: dict):
        self.config = config

    def validate_trade(self, signal: dict, active_positions: dict, daily_pnl: float) -> dict:
        """Valida se um trade deve ser executado"""

        # 1. Numero de posicoes
        if len(active_positions) >= self.config["max_positions"]:
            return {"approved": False, "reason": f"Maximo de posicoes atingido ({self.config['max_positions']})"}

        # 2. REMOVIDO: verificacao de daily_pnl aqui — feita corretamente em run_autonomous()
        #    (daily_pnl eh em dolares, nao fracao — nao comparar com 0.05)

        # 3. REMOVIDO: verificacao de confianca aqui — feita em run_autonomous() com CONFIG["min_confidence"]
        #    Nao duplicar logica contraditoria

        # 4. R:R minimo
        if not self._validate_rr(signal):
            return {"approved": False, "reason": "R:R abaixo do minimo (1:2)"}

        # 5. Correlacao — nao abrir 2 posicoes muito correlatas na mesma direcao
        symbol    = signal.get("symbol", "")
        direction = signal.get("signal", "")
        if self._check_correlation_risk(symbol, direction, active_positions):
            return {"approved": False, "reason": "Risco de correlacao alto — par similar ja em posicao"}

        return {"approved": True, "reason": "Trade aprovado"}

    def _validate_rr(self, signal: dict) -> bool:
        try:
            entry = float(signal.get("entry_price", 0))
            sl    = float(signal.get("stop_loss", 0))
            tp1   = float(signal.get("take_profit_1", 0))
            if not all([entry, sl, tp1]):
                return False
            risk   = abs(entry - sl)
            reward = abs(tp1 - entry)
            if risk == 0:
                return False
            return (reward / risk) >= self.config.get("take_profit_ratio", 2.0)
        except Exception:
            return False

    def _check_correlation_risk(self, symbol: str, direction: str, active_positions: dict) -> bool:
        """Bloqueia apenas posicoes na MESMA direcao em ativos altamente correlatos"""
        correlation_groups = {
            "btc_eth":  ["BTC", "ETH"],
            "sol_avax": ["SOL", "AVAX", "APT", "SUI"],
            "defi":     ["ARB", "OP", "INJ"],
        }
        for group_assets in correlation_groups.values():
            if symbol in group_assets:
                for active_sym, pos in active_positions.items():
                    if active_sym in group_assets and active_sym != symbol:
                        if pos.get("direction") == direction:
                            return True
        return False

    def calculate_position_size(self, account_value: float, entry: float, stop_loss: float, risk_pct: float = None) -> float:
        """Calcula tamanho da posicao pelo risco"""
        if risk_pct is None:
            risk_pct = self.config.get("risk_per_trade", 0.02)
        if entry == 0 or stop_loss == 0:
            return 0
        risk_amount      = account_value * risk_pct
        price_risk_pct   = abs(entry - stop_loss) / entry
        if price_risk_pct == 0:
            return 0
        position_value   = risk_amount / price_risk_pct
        position_size    = position_value / entry
        max_lev          = self.config.get("max_leverage", 20)
        max_size         = (account_value * max_lev) / entry
        final_size       = min(position_size, max_size)
        logger.info(f"Position Size: {final_size:.6f} | Risk: ${risk_amount:.2f} | PricRisk: {price_risk_pct*100:.2f}%")
        return round(final_size, 6)

    def calculate_trailing_stop(self, current_price: float, direction: str, atr: float) -> float:
        """Calcula trailing stop dinamico baseado em ATR"""
        multiplier = 2.0
        if direction == "LONG":
            return current_price - (atr * multiplier)
        else:
            return current_price + (atr * multiplier)

    def get_risk_metrics(self, account_value: float, positions: dict) -> dict:
        total_exposure = sum(p.get("size", 0) * p.get("entry", 0) for p in positions.values())
        exposure_pct   = (total_exposure / account_value * 100) if account_value > 0 else 0
        return {
            "account_value":   account_value,
            "total_exposure":  total_exposure,
            "exposure_pct":    round(exposure_pct, 2),
            "positions_count": len(positions),
            "risk_level":      "HIGH" if exposure_pct > 50 else "MEDIUM" if exposure_pct > 25 else "LOW"
        }