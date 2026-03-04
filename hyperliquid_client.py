"""
Hyperliquid API Client
Interface com a API da Hyperliquid — assinatura EIP-712 via SDK oficial
"""

import time
import requests
import logging

logger = logging.getLogger("HyperliquidClient")


class HyperliquidClient:

    MAINNET_URL = "https://api.hyperliquid.xyz"
    TESTNET_URL = "https://api.hyperliquid-testnet.xyz"

    def __init__(self, wallet_address: str, private_key: str, testnet: bool = True):
        self.wallet_address = wallet_address.lower() if wallet_address else ""
        self.private_key    = private_key
        self.base_url       = self.TESTNET_URL if testnet else self.MAINNET_URL
        self.testnet        = testnet
        self.session        = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

        self._exchange = None
        self._info     = None
        if private_key:
            self._init_sdk()

    def _init_sdk(self):
        try:
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
            from eth_account import Account

            account        = Account.from_key(self.private_key)
            self._exchange = Exchange(
                account, self.base_url,
                account_address=self.wallet_address or account.address
            )
            self._info = Info(self.base_url, skip_ws=True)
            logger.info("SDK hyperliquid-python-sdk inicializado (EIP-712)")
        except ImportError as e:
            logger.error(
                f"SDK nao instalado: {e}\n"
                "Execute: pip install git+https://github.com/hyperliquid-dex/hyperliquid-python-sdk.git eth-account"
            )
        except Exception as e:
            logger.error(f"Erro ao inicializar SDK: {e}")

    # ══════════════════════════════════════════════
    #  HTTP interno
    # ══════════════════════════════════════════════

    def _post(self, endpoint: str, payload: dict) -> dict:
        url  = f"{self.base_url}/{endpoint}"
        resp = self.session.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()

    # ══════════════════════════════════════════════
    #  MARKET DATA
    # ══════════════════════════════════════════════

    def get_all_mids(self) -> dict:
        try:
            r = self._post("info", {"type": "allMids"})
            return r if r else {}
        except Exception:
            return {}

    def get_mark_price(self, symbol: str) -> float:
        return float(self.get_all_mids().get(symbol, 0))

    def get_meta(self) -> dict:
        try:
            return self._post("info", {"type": "meta"})
        except Exception:
            return {}

    def get_l2_book(self, symbol: str) -> dict:
        try:
            r = self._post("info", {"type": "l2Book", "coin": symbol})
            return r if r else None
        except Exception:
            return None

    def get_candles(self, symbol: str, interval: str = "1h", lookback: int = 100) -> list:
        try:
            end_time    = int(time.time() * 1000)
            interval_ms = {
                "1m": 60_000, "3m": 180_000, "5m": 300_000,
                "15m": 900_000, "30m": 1_800_000,
                "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000
            }.get(interval, 3_600_000)
            start_time = end_time - (lookback * interval_ms)
            r = self._post("info", {
                "type": "candleSnapshot",
                "req":  {"coin": symbol, "interval": interval,
                         "startTime": start_time, "endTime": end_time}
            })
            return r if r else []
        except Exception:
            return []

    def get_funding_rate(self, symbol: str) -> dict:
        try:
            meta = self.get_meta()
            for asset in meta.get("universe", []):
                if asset.get("name") == symbol:
                    return {
                        "funding_rate":  float(asset.get("funding", 0)),
                        "open_interest": float(asset.get("openInterest", 0)),
                        "mark_price":    float(asset.get("markPx", 0)),
                    }
        except Exception:
            pass
        return {"funding_rate": 0, "open_interest": 0, "mark_price": 0}

    def get_recent_trades(self, symbol: str) -> list:
        try:
            r = self._post("info", {"type": "recentTrades", "coin": symbol})
            return r[:50] if r else []
        except Exception:
            return []

    # ══════════════════════════════════════════════
    #  ACCOUNT — com suporte a conta unificada
    # ══════════════════════════════════════════════

    def get_account_info(self) -> dict:
        if not self.wallet_address:
            return {"equity": 1000, "margin_used": 0, "free_margin": 1000, "positions": []}
        try:
            r      = self._post("info", {"type": "clearinghouseState", "user": self.wallet_address})
            equity = float(r.get("marginSummary", {}).get("accountValue", 0))
            margin = float(r.get("marginSummary", {}).get("totalMarginUsed", 0))

            # Conta unificada (testnet): se equity==0, saldo esta no Spot
            # A API retorna marginSummary zerado quando nao ha posicoes abertas em perps
            if equity == 0:
                try:
                    spot = self._post("info", {
                        "type": "spotClearinghouseState",
                        "user": self.wallet_address
                    })
                    for bal in spot.get("balances", []):
                        if bal.get("coin") == "USDC":
                            equity = float(bal.get("total", 0))
                            break
                except Exception:
                    pass

            return {
                "equity":      equity,
                "margin_used": margin,
                "free_margin": equity - margin,
                "positions":   r.get("assetPositions", []),
                "raw":         r,
            }
        except Exception as e:
            logger.error(f"Erro get_account_info: {e}")
            return {"equity": 0, "margin_used": 0, "free_margin": 0, "positions": [], "error": str(e)}

    def get_open_orders(self) -> list:
        if not self.wallet_address:
            return []
        try:
            return self._post("info", {"type": "openOrders", "user": self.wallet_address}) or []
        except Exception:
            return []

    # ══════════════════════════════════════════════
    #  ORDER EXECUTION — via SDK oficial (EIP-712)
    # ══════════════════════════════════════════════

    def set_leverage(self, symbol: str, leverage: int, is_cross: bool = False) -> dict:
        if not self._exchange:
            raise RuntimeError("SDK nao inicializado")
        result = self._exchange.update_leverage(leverage, symbol, is_cross)
        logger.info(f"Leverage {symbol}: {leverage}x ({'cross' if is_cross else 'isolated'})")
        return result

    def place_order(self, symbol: str, is_buy: bool, size: float, price: float,
                    order_type: str = "limit", reduce_only: bool = False,
                    tif: str = "Gtc") -> dict:
        if not self._exchange:
            raise RuntimeError("SDK nao inicializado")

        if order_type == "market":
            ot_sdk = {"market": {}}
        elif order_type == "stop":
            ot_sdk = {"trigger": {
                "isMarket":  True,
                "triggerPx": str(round(price, 6)),
                "tpsl":      "sl"
            }}
        else:
            ot_sdk = {"limit": {"tif": tif}}

        result = self._exchange.order(symbol, is_buy, size, price, ot_sdk, reduce_only=reduce_only)

        if result.get("status") == "ok":
            statuses = result.get("response", {}).get("data", {}).get("statuses", [{}])
            st = statuses[0] if statuses else {}
            # Hyperliquid retorna oid em resting (limit nao preenchida),
            # filled (preenchida imediatamente) ou triggered (stop ativado)
            order_id = (
                st.get("resting", {}).get("oid") or
                st.get("filled",  {}).get("oid") or
                st.get("triggered", {}).get("oid") or
                # Fallback: busca oid em qualquer nivel do status
                next((v.get("oid") for v in st.values() if isinstance(v, dict) and "oid" in v), None)
            )
            logger.info(f"Ordem OK: {symbol} {'BUY' if is_buy else 'SELL'} {size} @ {price} oid={order_id} status={list(st.keys())}")
            return {"status": "ok", "order_id": order_id, "raw": result}
        else:
            logger.error(f"Ordem falhou: {result}")
            return {"status": "error", "raw": result}

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        if not self._exchange:
            raise RuntimeError("SDK nao inicializado")
        return self._exchange.cancel(symbol, order_id)

    def close_position(self, symbol: str, size: float, is_long: bool) -> dict:
        price     = self.get_mark_price(symbol)
        slippage  = 0.002
        lim_price = price * (1 - slippage) if is_long else price * (1 + slippage)
        return self.place_order(
            symbol=symbol, is_buy=not is_long, size=size,
            price=lim_price, order_type="market", reduce_only=True
        )

    # ══════════════════════════════════════════════
    #  ASSET INFO
    # ══════════════════════════════════════════════

    def get_asset_info(self) -> dict:
        try:
            meta   = self.get_meta()
            result = {}
            for idx, asset in enumerate(meta.get("universe", [])):
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
        return self.get_asset_info().get(symbol.upper(), {}).get("maxLeverage", 20)