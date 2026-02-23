"""
Risk Manager
Gerenciamento profissional de risco para trading de derivativos
"""

import logging

logger = logging.getLogger('RiskManager')


class RiskManager:
    """Gerenciador de risco profissional"""
    
    def __init__(self, config: dict):
        self.config = config
        self.daily_trades = []
        self.session_pnl = 0
    
    def validate_trade(self, signal: dict, active_positions: dict, daily_pnl: float) -> dict:
        """Valida se um trade deve ser executado baseado em regras de risco"""
        
        # 1. Verificar número de posições
        if len(active_positions) >= self.config["max_positions"]:
            return {"approved": False, "reason": f"Máximo de posições atingido ({self.config['max_positions']})"}
        
        # 2. Verificar perda diária
        if daily_pnl < -self.config.get("daily_loss_limit", 0.05):
            return {"approved": False, "reason": "Limite de perda diária atingido"}
        
        # 3. Verificar confiança mínima
        if signal.get("confidence", 0) < 65:
            return {"approved": False, "reason": f"Confiança insuficiente: {signal.get('confidence')}%"}
        
        # 4. Verificar R:R
        if not self._validate_rr(signal):
            return {"approved": False, "reason": "R:R abaixo do mínimo (1:2)"}
        
        # 5. Verificar alavancagem
        leverage = signal.get("leverage", self.config["default_leverage"])
        if leverage > self.config["max_leverage"]:
            signal["leverage"] = self.config["max_leverage"]
            logger.warning(f"Alavancagem reduzida para máximo: {self.config['max_leverage']}x")
        
        # 6. Verificar correlação (não abrir 2 posições muito correlatas)
        symbol = signal.get("symbol", "")
        if self._check_correlation_risk(symbol, active_positions):
            return {"approved": False, "reason": "Risco de correlação muito alto"}
        
        return {"approved": True, "reason": "Trade aprovado"}
    
    def _validate_rr(self, signal: dict) -> bool:
        """Valida se o Risk:Reward é adequado"""
        try:
            entry = signal.get("entry_price", 0)
            sl = signal.get("stop_loss", 0)
            tp1 = signal.get("take_profit_1", 0)
            
            if not all([entry, sl, tp1]):
                return False
            
            risk = abs(entry - sl)
            reward = abs(tp1 - entry)
            
            if risk == 0:
                return False
            
            rr = reward / risk
            min_rr = self.config.get("take_profit_ratio", 2.0)
            
            return rr >= min_rr
        except:
            return False
    
    def _check_correlation_risk(self, symbol: str, active_positions: dict) -> bool:
        """Verifica se há posições muito correlatas abertas"""
        # Grupos de alta correlação
        correlation_groups = {
            "btc_group": ["BTC", "ETH"],
            "alt_layer1": ["SOL", "AVAX", "NEAR", "APT", "SUI"],
            "defi": ["ARB", "OP", "DYDX", "GMX"],
        }
        
        for group_name, group_assets in correlation_groups.items():
            if symbol in group_assets:
                # Verificar se já temos posição em ativo do mesmo grupo
                for active_sym in active_positions.keys():
                    if active_sym in group_assets and active_sym != symbol:
                        return True  # Risco de correlação
        
        return False
    
    def calculate_position_size(
        self, 
        account_value: float, 
        entry: float, 
        stop_loss: float,
        risk_pct: float = None
    ) -> float:
        """
        Calcula o tamanho da posição baseado no risco
        
        Fórmula: Size = (Account * Risk%) / (|Entry - SL| / Entry)
        """
        if risk_pct is None:
            risk_pct = self.config.get("risk_per_trade", 0.02)
        
        if entry == 0 or stop_loss == 0:
            return 0
        
        risk_amount = account_value * risk_pct
        price_risk_pct = abs(entry - stop_loss) / entry
        
        if price_risk_pct == 0:
            return 0
        
        # Tamanho em USDC
        position_value_usdc = risk_amount / price_risk_pct
        
        # Tamanho em unidades do ativo
        position_size = position_value_usdc / entry
        
        # Limitar pelo capital disponível considerando alavancagem
        max_position_value = account_value * self.config.get("max_leverage", 10)
        max_size = max_position_value / entry
        
        final_size = min(position_size, max_size)
        
        logger.info(f"💰 Position Size: {final_size:.6f} | Risk: ${risk_amount:.2f} | "
                   f"Price Risk: {price_risk_pct*100:.2f}%")
        
        return round(final_size, 6)
    
    def calculate_trailing_stop(
        self, 
        entry: float, 
        current_price: float, 
        direction: str,
        atr: float
    ) -> float:
        """Calcula trailing stop dinâmico baseado em ATR"""
        atr_multiplier = 2.0
        
        if direction == "LONG":
            trailing_stop = current_price - (atr * atr_multiplier)
            return trailing_stop
        else:
            trailing_stop = current_price + (atr * atr_multiplier)
            return trailing_stop
    
    def get_risk_metrics(self, account_value: float, positions: dict) -> dict:
        """Retorna métricas de risco da carteira atual"""
        total_exposure = sum(
            pos.get("size", 0) * pos.get("entry", 0) 
            for pos in positions.values()
        )
        
        exposure_pct = (total_exposure / account_value * 100) if account_value > 0 else 0
        
        return {
            "account_value": account_value,
            "total_exposure": total_exposure,
            "exposure_pct": round(exposure_pct, 2),
            "positions_count": len(positions),
            "risk_level": "HIGH" if exposure_pct > 50 else "MEDIUM" if exposure_pct > 25 else "LOW"
        }
