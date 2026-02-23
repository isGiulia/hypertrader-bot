"""
Hyperliquid API Client
Interface com a API da Hyperliquid para dados e execução de ordens
"""

import json
import time
import hashlib
import hmac
import requests
from typing import Optional


class HyperliquidClient:
    """Cliente para a API da Hyperliquid"""
    
    MAINNET_URL = "https://api.hyperliquid.xyz"
    TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
    
    def __init__(self, wallet_address: str, private_key: str, testnet: bool = True):
        self.wallet_address = wallet_address
        self.private_key = private_key
        self.base_url = self.TESTNET_URL if testnet else self.MAINNET_URL
        self.testnet = testnet
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def _post(self, endpoint: str, payload: dict) -> dict:
        """Requisição POST para a API"""
        url = f"{self.base_url}/{endpoint}"
        response = self.session.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    
    def _sign_action(self, action: dict) -> dict:
        """Assina uma ação com a chave privada (simplificado)"""
        # Em produção, use a biblioteca oficial: hyperliquid-python-sdk
        # pip install hyperliquid-python
        nonce = int(time.time() * 1000)
        return {
            "action": action,
            "nonce": nonce,
            "signature": self._create_signature(action, nonce),
            "vaultAddress": None
        }
    
    def _create_signature(self, action: dict, nonce: int) -> dict:
        """Cria assinatura EIP-712 (simplificado - use SDK oficial em produção)"""
        # IMPORTANTE: Em produção use a biblioteca hyperliquid-python-sdk
        # que implementa EIP-712 corretamente
        # pip install hyperliquid-python
        return {"r": "0x" + "0"*64, "s": "0x" + "0"*64, "v": 27}
    
    # ══════════════════════════════════════════════
    #  MARKET DATA
    # ══════════════════════════════════════════════
    
    def get_all_mids(self) -> dict:
        """Retorna preços mid de todos os ativos"""
        try:
            result = self._post("info", {"type": "allMids"})
            return result if result else {}
        except Exception as e:
            return {}
    
    def get_mark_price(self, symbol: str) -> float:
        """Retorna o preço mark de um símbolo"""
        mids = self.get_all_mids()
        return float(mids.get(symbol, 0))
    
    def get_meta(self) -> dict:
        """Retorna metadados de todos os mercados"""
        try:
            return self._post("info", {"type": "meta"})
        except:
            return {}
    
    def get_l2_book(self, symbol: str) -> dict:
        """Retorna o livro de ordens L2"""
        try:
            return self._post("info", {
                "type": "l2Book",
                "coin": symbol
            })
        except:
            return {"levels": [[], []]}
    
    def get_candles(self, symbol: str, interval: str = "1h", lookback: int = 100) -> list:
        """
        Retorna candles OHLCV
        interval: "1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"
        """
        try:
            end_time = int(time.time() * 1000)
            interval_ms = {
                "1m": 60000, "3m": 180000, "5m": 300000,
                "15m": 900000, "30m": 1800000,
                "1h": 3600000, "4h": 14400000, "1d": 86400000
            }.get(interval, 3600000)
            
            start_time = end_time - (lookback * interval_ms)
            
            result = self._post("info", {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": interval,
                    "startTime": start_time,
                    "endTime": end_time
                }
            })
            return result if result else []
        except:
            return []
    
    def get_funding_rate(self, symbol: str) -> dict:
        """Retorna funding rate atual"""
        try:
            meta = self.get_meta()
            universe = meta.get("universe", [])
            for asset in universe:
                if asset.get("name") == symbol:
                    return {
                        "funding_rate": float(asset.get("funding", 0)),
                        "open_interest": float(asset.get("openInterest", 0)),
                        "mark_price": float(asset.get("markPx", 0))
                    }
        except:
            pass
        return {"funding_rate": 0, "open_interest": 0, "mark_price": 0}
    
    def get_open_interest(self) -> list:
        """Retorna open interest de todos os mercados"""
        try:
            return self._post("info", {"type": "openInterest"})
        except:
            return []
    
    def get_recent_trades(self, symbol: str) -> list:
        """Retorna trades recentes"""
        try:
            result = self._post("info", {
                "type": "recentTrades",
                "coin": symbol
            })
            return result[:50] if result else []
        except:
            return []
    
    # ══════════════════════════════════════════════
    #  ACCOUNT
    # ══════════════════════════════════════════════
    
    def get_account_info(self) -> dict:
        """Retorna informações da conta"""
        if not self.wallet_address:
            return {"equity": 1000, "margin_used": 0, "positions": []}
        try:
            result = self._post("info", {
                "type": "clearinghouseState",
                "user": self.wallet_address
            })
            
            equity = float(result.get("marginSummary", {}).get("accountValue", 0))
            margin_used = float(result.get("marginSummary", {}).get("totalMarginUsed", 0))
            
            return {
                "equity": equity,
                "margin_used": margin_used,
                "free_margin": equity - margin_used,
                "positions": result.get("assetPositions", []),
                "raw": result
            }
        except Exception as e:
            return {"equity": 0, "margin_used": 0, "positions": [], "error": str(e)}
    
    def get_open_orders(self) -> list:
        """Retorna ordens abertas"""
        if not self.wallet_address:
            return []
        try:
            return self._post("info", {
                "type": "openOrders",
                "user": self.wallet_address
            })
        except:
            return []
    
    # ══════════════════════════════════════════════
    #  ORDER EXECUTION
    # ══════════════════════════════════════════════
    
    def set_leverage(self, symbol: str, leverage: int, is_cross: bool = False) -> dict:
        """Define alavancagem para um símbolo"""
        action = {
            "type": "updateLeverage",
            "asset": self._get_asset_index(symbol),
            "isCross": is_cross,
            "leverage": leverage
        }
        signed = self._sign_action(action)
        return self._post("exchange", signed)
    
    def place_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float,
        order_type: str = "limit",
        reduce_only: bool = False,
        tif: str = "Gtc"
    ) -> dict:
        """
        Coloca uma ordem na Hyperliquid
        order_type: 'limit', 'market', 'stop'
        tif: 'Gtc', 'Ioc', 'Alo'
        """
        
        asset_idx = self._get_asset_index(symbol)
        
        if order_type == "market":
            order_spec = {"market": {}}
        elif order_type == "stop":
            order_spec = {
                "trigger": {
                    "isMarket": True,
                    "triggerPx": str(round(price, 6)),
                    "tpsl": "sl"
                }
            }
        else:  # limit
            order_spec = {"limit": {"tif": tif}}
        
        action = {
            "type": "order",
            "orders": [{
                "a": asset_idx,
                "b": is_buy,
                "p": str(round(price, 6)),
                "s": str(round(size, 6)),
                "r": reduce_only,
                "t": order_spec
            }],
            "grouping": "na"
        }
        
        signed = self._sign_action(action)
        return self._post("exchange", signed)
    
    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Cancela uma ordem"""
        action = {
            "type": "cancel",
            "cancels": [{
                "a": self._get_asset_index(symbol),
                "o": order_id
            }]
        }
        signed = self._sign_action(action)
        return self._post("exchange", signed)
    
    def close_position(self, symbol: str, size: float, is_long: bool) -> dict:
        """Fecha uma posição a mercado"""
        price = self.get_mark_price(symbol)
        slippage = 0.002  # 0.2% slippage tolerance
        
        if is_long:  # Fechar long = vender
            limit_price = price * (1 - slippage)
        else:  # Fechar short = comprar
            limit_price = price * (1 + slippage)
        
        return self.place_order(
            symbol=symbol,
            is_buy=not is_long,
            size=size,
            price=limit_price,
            order_type="market",
            reduce_only=True
        )
    
    def _get_asset_index(self, symbol: str) -> int:
        """Retorna o índice do ativo via API (com fallback local)."""
        try:
            info = self.get_asset_info()
            if symbol.upper() in info:
                return info[symbol.upper()]["index"]
        except Exception:
            pass
        ASSET_MAP = {
            "BTC": 0, "ETH": 1, "ATOM": 2, "MATIC": 3, "DYDX": 4,
            "SOL": 5, "BNB": 6, "APT": 7, "ARB": 8, "AVAX": 9,
            "OP": 10, "LTC": 11, "DOGE": 12, "ADA": 13, "LINK": 14,
            "SUI": 15, "TRX": 16, "XRP": 17, "INJ": 18, "RNDR": 19,
            "WIF": 20, "PEPE": 21, "TIA": 22,
        }
        return ASSET_MAP.get(symbol.upper(), 0)

    def get_asset_info(self) -> dict:
        """
        Retorna metadados de todos os ativos incluindo maxLeverage real da plataforma.
        Resultado: { "BTC": {"index": 0, "maxLeverage": 50, "szDecimals": 5}, ... }
        """
        try:
            meta = self.get_meta()
            universe = meta.get("universe", [])
            result = {}
            for idx, asset in enumerate(universe):
                name = asset.get("name", "")
                result[name] = {
                    "index":        idx,
                    "maxLeverage":  int(asset.get("maxLeverage", 20)),
                    "szDecimals":   int(asset.get("szDecimals", 4)),
                    "onlyIsolated": asset.get("onlyIsolated", False),
                }
            return result
        except Exception:
            return {}

    def get_max_leverage(self, symbol: str) -> int:
        """Retorna a alavancagem máxima permitida pela Hyperliquid para o símbolo."""
        info = self.get_asset_info()
        return info.get(symbol.upper(), {}).get("maxLeverage", 20)
