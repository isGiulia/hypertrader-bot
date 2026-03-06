"""
Hyperliquid API Client — v2
Interface completa com a API da Hyperliquid
- Assinatura EIP-712 via SDK oficial
- userFills: preço real, fee, P&L fechado, timestamp
- userFunding: funding acumulado por posição
- historicalOrders: histórico completo de ordens
- orderStatus: status individual de ordem
- metaAndAssetCtxs: dados de mercado em batch (1 request)
- Tick/lot size: arredondamento correto de preços e sizes
"""

import time
import logging
from datetime import datetime, timezone
from math import log10, floor

import requests

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

        self._exchange   = None
        self._info       = None
        self._asset_info = {}   # cache: sym -> metadados

        # Cache curto de preços para evitar spam de requests e evitar preço "travado"
        self._mids_cache: dict = {}
        self._mids_cache_ts: float = 0.0

        if private_key:
            self._init_sdk()

    # ══════════════════════════════════════════════
    #  SDK
    # ══════════════════════════════════════════════

    def _init_sdk(self):
        try:
            from hyperliquid.exchange import Exchange
            from hyperliquid.info     import Info
            from eth_account          import Account

            account        = Account.from_key(self.private_key)
            self._exchange = Exchange(
                account, self.base_url,
                account_address=self.wallet_address or account.address
            )
            self._info = Info(self.base_url, skip_ws=True)
            logger.info("SDK hyperliquid-python-sdk inicializado (EIP-712)")
            self._load_asset_info()
        except ImportError as e:
            logger.error(f"SDK nao instalado: {e}")
        except Exception as e:
            logger.error(f"Erro ao inicializar SDK: {e}")

    # ══════════════════════════════════════════════
    #  HTTP interno
    # ══════════════════════════════════════════════

    def _post(self, endpoint: str, payload: dict, timeout: int = 10) -> dict:
        url  = f"{self.base_url}/{endpoint}"
        resp = self.session.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # ══════════════════════════════════════════════
    #  ASSET INFO + TICK/LOT SIZE
    # ══════════════════════════════════════════════

    def _load_asset_info(self):
        """Carrega metadados de todos os assets em 1 request (metaAndAssetCtxs)."""
        try:
            r    = self._post("info", {"type": "metaAndAssetCtxs"})
            meta = r[0] if isinstance(r, list) and len(r) > 0 else {}
            ctxs = r[1] if isinstance(r, list) and len(r) > 1 else []

            for idx, asset in enumerate(meta.get("universe", [])):
                name = asset.get("name", "")
                ctx  = ctxs[idx] if idx < len(ctxs) else {}
                self._asset_info[name] = {
                    "index":        idx,
                    "szDecimals":   int(asset.get("szDecimals", 4)),
                    "maxLeverage":  int(asset.get("maxLeverage", 20)),
                    "onlyIsolated": asset.get("onlyIsolated", False),
                    "markPx":       float(ctx.get("markPx", 0) or 0),
                    "funding":      float(ctx.get("funding", 0) or 0),
                    "openInterest": float(ctx.get("openInterest", 0) or 0),
                }
            logger.info(f"Asset info: {len(self._asset_info)} assets carregados")
        except Exception as e:
            logger.error(f"_load_asset_info: {e}")

    def get_asset_info(self) -> dict:
        if not self._asset_info:
            self._load_asset_info()
        return self._asset_info

    def get_max_leverage(self, symbol: str) -> int:
        return self.get_asset_info().get(symbol.upper(), {}).get("maxLeverage", 20)

    def get_sz_decimals(self, symbol: str) -> int:
        return self.get_asset_info().get(symbol, {}).get("szDecimals", 4)

    def round_size(self, symbol: str, size: float) -> float:
        """Arredonda size para o lot size correto da Hyperliquid."""
        decimals = self.get_sz_decimals(symbol)
        return round(float(size), decimals)

    def round_price(self, price: float) -> float:
        """
        Arredonda preço seguindo a regra da Hyperliquid:
        máximo 5 algarismos significativos.
        """
        if price <= 0:
            return price
        try:
            magnitude = floor(log10(abs(price)))
            factor    = 10 ** (4 - magnitude)
            return round(round(price * factor) / factor, max(0, 4 - magnitude))
        except Exception:
            return round(price, 2)

    # ══════════════════════════════════════════════
    #  MARKET DATA
    # ══════════════════════════════════════════════

    def get_all_mids(self) -> dict:
        try:
            r = self._post("info", {"type": "allMids"})
            return r if r else {}
        except Exception:
            return {}

    def _get_all_mids_cached(self, ttl_seconds: float = 2.0) -> dict:
        """Retorna mids com cache de TTL curto para evitar rate-limit."""
        now = time.time()
        if self._mids_cache and (now - self._mids_cache_ts) <= ttl_seconds:
            return self._mids_cache
        mids = self.get_all_mids() or {}
        if mids:
            self._mids_cache = mids
            self._mids_cache_ts = now
        return self._mids_cache

    def get_mark_price(self, symbol: str) -> float:
        """Preço atual para cálculos (PnL, decisão market/limit, trailing).

        IMPORTANTE: markPx em metaAndAssetCtxs é carregado uma vez no boot e pode ficar stale.
        Aqui usamos allMids com cache curto (TTL ~2s), que reflete o preço atual e evita
        ficar "congelado".
        """
        sym = symbol.upper()
        mids = self._get_all_mids_cached(ttl_seconds=2.0)
        try:
            px = float(mids.get(sym, 0) or 0)
            if px > 0:
                return px
        except Exception:
            pass

        # fallback: último markPx conhecido (pode estar desatualizado, mas melhor que 0)
        try:
            info = self.get_asset_info()
            if sym in info and info[sym].get("markPx", 0) > 0:
                return float(info[sym]["markPx"])
        except Exception:
            pass
        return 0.0

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
            info = self.get_asset_info()
            if symbol in info:
                return {
                    "funding_rate":  info[symbol].get("funding", 0),
                    "open_interest": info[symbol].get("openInterest", 0),
                    "mark_price":    info[symbol].get("markPx", 0),
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
    #  ACCOUNT
    # ══════════════════════════════════════════════

    def get_account_info(self) -> dict:
        if not self.wallet_address:
            return {"equity": 1000, "margin_used": 0, "free_margin": 1000, "positions": []}
        try:
            r      = self._post("info", {"type": "clearinghouseState", "user": self.wallet_address})
            ms     = r.get("marginSummary", {})
            equity = float(ms.get("accountValue", 0))
            margin = float(ms.get("totalMarginUsed", 0))

            # withdrawable = saldo realmente disponivel para novas ordens
            # É o campo correto para verificar se pode abrir nova posição
            withdrawable = float(r.get("withdrawable", 0) or 0)
            free_margin  = withdrawable if withdrawable > 0 else max(equity - margin, 0)

            if equity == 0:
                try:
                    spot = self._post("info", {"type": "spotClearinghouseState", "user": self.wallet_address})
                    for bal in spot.get("balances", []):
                        if bal.get("coin") == "USDC":
                            equity = float(bal.get("total", 0))
                            free_margin = equity
                            break
                except Exception:
                    pass

            logger.debug(f"Account: equity=${equity:.2f} margin=${margin:.2f} free=${free_margin:.2f} withdrawable=${withdrawable:.2f}")
            return {
                "equity":      equity,
                "margin_used": margin,
                "free_margin": free_margin,
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

    def get_all_positions(self) -> dict:
        """Retorna todas as posições abertas como dict sym -> dados."""
        result = {}
        try:
            acc = self.get_account_info()
            for p in acc.get("positions", []):
                pd  = p.get("position", {})
                sym = pd.get("coin", "")
                sz  = float(pd.get("szi", 0))
                if abs(sz) < 0.0001:
                    continue
                lev_raw = pd.get("leverage", {})
                lev     = float(lev_raw.get("value", 1) if isinstance(lev_raw, dict) else lev_raw or 1)
                result[sym] = {
                    "is_long":        sz > 0,
                    "size":           abs(sz),
                    "entry_price":    float(pd.get("entryPx", 0) or 0),
                    "unrealized_pnl": float(pd.get("unrealizedPnl", 0) or 0),
                    "liq_price":      float(pd.get("liquidationPx", 0) or 0),
                    "margin_used":    float(pd.get("marginUsed", 0) or 0),
                    "leverage":       lev,
                }
        except Exception as e:
            logger.error(f"get_all_positions: {e}")
        return result

    # ══════════════════════════════════════════════
    #  USER FILLS — preço real, fee, P&L, timestamp
    # ══════════════════════════════════════════════

    def get_user_fills(self, symbol: str = None) -> list:
        """
        Ordens preenchidas com detalhes completos:
          px, sz, side, time, fee, closedPnl, dir, oid, tid
        """
        if not self.wallet_address:
            return []
        try:
            r     = self._post("info", {"type": "userFills", "user": self.wallet_address})
            fills = r if isinstance(r, list) else []
            if symbol:
                fills = [f for f in fills if f.get("coin") == symbol]
            return sorted(fills, key=lambda x: x.get("time", 0), reverse=True)
        except Exception as e:
            logger.error(f"get_user_fills: {e}")
            return []

    def get_last_fill(self, symbol: str) -> dict:
        """Fill mais recente de um símbolo com todos os detalhes."""
        fills = self.get_user_fills(symbol)
        if not fills:
            return {}
        f  = fills[0]
        ts = datetime.fromtimestamp(f.get("time", 0) / 1000, tz=timezone.utc)
        return {
            "price":        float(f.get("px", 0)),
            "size":         float(f.get("sz", 0)),
            "side":         "BUY" if f.get("side") == "B" else "SELL",
            "fee":          float(f.get("fee", 0)),
            "closed_pnl":   float(f.get("closedPnl", 0)),
            "direction":    f.get("dir", ""),
            "order_id":     f.get("oid"),
            "trade_id":     f.get("tid"),
            "timestamp":    ts.strftime("%d/%m %H:%M:%S UTC"),
            "timestamp_ms": f.get("time", 0),
        }

    def get_fills_summary(self, symbol: str = None) -> dict:
        """Resumo: total fees, P&L realizado, contagem de trades."""
        fills     = self.get_user_fills(symbol)
        total_fee = sum(float(f.get("fee", 0)) for f in fills)
        total_pnl = sum(float(f.get("closedPnl", 0)) for f in fills)
        return {
            "total_trades": len(fills),
            "opens":        len([f for f in fills if "Open"  in f.get("dir", "")]),
            "closes":       len([f for f in fills if "Close" in f.get("dir", "")]),
            "total_fee":    round(total_fee, 4),
            "total_pnl":    round(total_pnl, 4),
        }

    # ══════════════════════════════════════════════
    #  HISTORICAL ORDERS
    # ══════════════════════════════════════════════

    def get_historical_orders(self, symbol: str = None) -> list:
        """Histórico completo de ordens: preenchidas, canceladas, abertas."""
        if not self.wallet_address:
            return []
        try:
            r      = self._post("info", {"type": "historicalOrders", "user": self.wallet_address})
            orders = r if isinstance(r, list) else []
            if symbol:
                orders = [o for o in orders if o.get("order", {}).get("coin") == symbol]
            return orders
        except Exception as e:
            logger.error(f"get_historical_orders: {e}")
            return []

    def get_order_status(self, order_id: int) -> dict:
        """Status de uma ordem específica: open / filled / canceled."""
        if not self.wallet_address or not order_id:
            return {}
        try:
            return self._post("info", {
                "type": "orderStatus",
                "user": self.wallet_address,
                "oid":  order_id
            }) or {}
        except Exception as e:
            logger.error(f"get_order_status {order_id}: {e}")
            return {}

    # ══════════════════════════════════════════════
    #  USER FUNDING
    # ══════════════════════════════════════════════

    def get_user_funding(self, lookback_hours: int = 24) -> list:
        """Funding pago/recebido nas últimas N horas."""
        if not self.wallet_address:
            return []
        try:
            end_time   = int(time.time() * 1000)
            start_time = end_time - (lookback_hours * 3_600_000)
            r = self._post("info", {
                "type":      "userFunding",
                "user":      self.wallet_address,
                "startTime": start_time,
                "endTime":   end_time,
            })
            return r if isinstance(r, list) else []
        except Exception as e:
            logger.error(f"get_user_funding: {e}")
            return []

    def get_funding_summary(self, symbol: str = None) -> dict:
        """Total de funding pago/recebido nas últimas 24h."""
        items = self.get_user_funding(24)
        if symbol:
            items = [i for i in items if i.get("delta", {}).get("coin") == symbol]
        total = sum(float(i.get("delta", {}).get("usdc", 0)) for i in items)
        return {
            "total_funding_usd": round(total, 4),
            "count":             len(items),
            "received":          total >= 0,
        }

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

        size  = self.round_size(symbol, float(size))
        price = float(price)

        if order_type == "market":
            slippage = 0.005
            price    = price * (1 + slippage) if is_buy else price * (1 - slippage)
            price    = self.round_price(price)
            ot_sdk   = {"limit": {"tif": "Ioc"}}
        elif order_type == "stop":
            price  = self.round_price(price)
            ot_sdk = {"trigger": {
                "isMarket":  True,
                "triggerPx": str(price),
                "tpsl":      "sl"
            }}
        else:
            price  = self.round_price(price)
            ot_sdk = {"limit": {"tif": tif}}

        logger.info(f"Ordem: {symbol} {'BUY' if is_buy else 'SELL'} sz={size} px={price} [{order_type}]")
        result = self._exchange.order(symbol, is_buy, size, price, ot_sdk, reduce_only=reduce_only)

        if result.get("status") == "ok":
            statuses = result.get("response", {}).get("data", {}).get("statuses", [{}])
            status_0 = statuses[0] if statuses else {}
            filled   = status_0.get("filled",  {})
            resting  = status_0.get("resting", {})
            order_id = filled.get("oid") or resting.get("oid")

            filled_sz = float(filled.get("totalSz", 0) or 0)
            avg_px    = float(filled.get("avgPx", price) or price)
            is_filled = filled_sz > 0

            logger.info(
                f"Ordem OK oid={order_id} "
                f"{'FILLED avgPx=' + str(avg_px) + ' sz=' + str(filled_sz) if is_filled else 'RESTING'}"
            )
            return {
                "status":    "ok",
                "order_id":  order_id,
                "filled":    is_filled,
                "filled_sz": filled_sz,
                "avg_px":    avg_px if is_filled else price,
                "raw":       result,
            }
        else:
            logger.error(f"Ordem falhou: {result}")
            return {"status": "error", "raw": result}

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        if not self._exchange:
            raise RuntimeError("SDK nao inicializado")
        try:
            return self._exchange.cancel(symbol, order_id)
        except Exception as e:
            logger.error(f"cancel_order {symbol} {order_id}: {e}")
            return {"status": "error", "error": str(e)}

    def cancel_all_orders(self, symbol: str = None) -> int:
        """Cancela todas as ordens abertas. Retorna quantidade cancelada."""
        orders   = self.get_open_orders()
        canceled = 0
        for o in orders:
            if symbol and o.get("coin") != symbol:
                continue
            try:
                self.cancel_order(o.get("coin"), o.get("oid"))
                canceled += 1
            except Exception as e:
                logger.error(f"cancel_all {o.get('coin')} {o.get('oid')}: {e}")
        return canceled

    def close_position(self, symbol: str, size: float, is_long: bool) -> dict:
        price     = self.get_mark_price(symbol)
        slippage  = 0.003
        lim_price = price * (1 - slippage) if is_long else price * (1 + slippage)
        return self.place_order(
            symbol=symbol, is_buy=not is_long, size=size,
            price=lim_price, order_type="market", reduce_only=True
        )