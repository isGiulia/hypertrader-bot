"""
Market Analyzer
Analise tecnica completa — sem warnings que bloqueiem o Claude
"""

import math
import time
import urllib.request
import json as _json
from typing import Optional

try:
    # Opcional: usa flags do V1 (macro/orderbook etc.)
    from hypertrader import config as _config
except Exception:
    _config = None


class MarketAnalyzer:

    def __init__(self, hl_client):
        self.hl                = hl_client
        self._macro_cache      = None
        self._macro_cache_time = 0

    # ══════════════════════════════════════════════
    #  MACRO CONTEXT — Fear & Greed + BTC Dominance
    # ══════════════════════════════════════════════

    def _get_macro_context(self) -> dict:
        """
        Retorna contexto macro (F&G + BTC Dom) com cache de 1h.

        IMPORTANTE: Os 'warning' aqui sao descritivos (informam o valor),
        NAO sao instrucoes de bloqueio. O system prompt do LLM e quem
        define o que fazer com cada faixa de F&G.
        """
        now = time.time()
        if self._macro_cache and (now - self._macro_cache_time) < 3600:
            return self._macro_cache

        result = {
            "fear_greed_index":      None,
            "fear_greed_label":      "DESCONHECIDO",
            "fear_greed_context":    "",
            "btc_dominance":         None,
            "btc_dominance_trend":   "desconhecido",
            "btc_dominance_context": "",
            "data_source":           "alternative.me + coingecko",
        }

        # ── Fear & Greed ──────────────────────────────────────────
        try:
            req  = urllib.request.urlopen(
                "https://api.alternative.me/fng/?limit=2&format=json", timeout=5)
            data = _json.loads(req.read().decode())
            fng  = data["data"]
            val  = int(fng[0]["value"])
            lbl  = fng[0]["value_classification"]
            result["fear_greed_index"] = val
            result["fear_greed_label"] = lbl

            # Contexto descritivo — sem instrucoes de bloqueio
            if val <= 25:
                result["fear_greed_context"] = (
                    f"F&G={val} (Medo Extremo). Mercado em pânico. "
                    f"Historicamente favoravel para SHORTs em downtrend "
                    f"e para LONGs contrarios em suporte forte com reversao confirmada."
                )
            elif val <= 45:
                result["fear_greed_context"] = (
                    f"F&G={val} (Medo). Sentimento negativo. "
                    f"SHORTs levemente favorecidos. LONGs precisam de confirmacao extra."
                )
            elif val <= 55:
                result["fear_greed_context"] = (
                    f"F&G={val} (Neutro). Sem viés de sentimento. Siga a tecnica."
                )
            elif val <= 75:
                result["fear_greed_context"] = (
                    f"F&G={val} (Ganancia). Sentimento positivo. "
                    f"LONGs levemente favorecidos."
                )
            else:
                result["fear_greed_context"] = (
                    f"F&G={val} (Ganancia Extrema). Mercado euforico — risco de topo. "
                    f"SHORTs favorecidos. LONGs precisam de momentum muito forte."
                )
        except Exception as e:
            result["fear_greed_context"] = f"Dado indisponivel: {e}. Assuma sentimento neutro."

        # ── BTC Dominance ─────────────────────────────────────────
        try:
            req  = urllib.request.urlopen(
                "https://api.coingecko.com/api/v3/global", timeout=5)
            data = _json.loads(req.read().decode())
            dom  = data.get("data", {}).get("market_cap_percentage", {}).get("btc")
            if dom is not None:
                dom = round(float(dom), 2)
                result["btc_dominance"] = dom
                if dom > 58:
                    result["btc_dominance_trend"]   = "muito_alta"
                    result["btc_dominance_context"] = (
                        f"BTC Dom={dom}% (muito alta). Capital concentrado no BTC. "
                        f"Altcoins em risco de queda relativa. Prefira BTC ou reduza confianca em LONG altcoins."
                    )
                elif dom > 55:
                    result["btc_dominance_trend"]   = "alta"
                    result["btc_dominance_context"] = (
                        f"BTC Dom={dom}% (alta). Altcoins perdendo forca relativa ao BTC. "
                        f"Reduza confianca em LONG altcoins."
                    )
                elif dom < 45:
                    result["btc_dominance_trend"]   = "baixa"
                    result["btc_dominance_context"] = (
                        f"BTC Dom={dom}% (baixa). Alt season possivel — altcoins com boa liquidez."
                    )
                else:
                    result["btc_dominance_trend"]   = "neutra"
                    result["btc_dominance_context"] = f"BTC Dom={dom}% (neutra). Sem impacto relevante."
        except Exception as e:
            result["btc_dominance_context"] = f"Dado indisponivel: {e}. Assuma dominancia neutra."

        self._macro_cache      = result
        self._macro_cache_time = time.time()
        return result

    # ══════════════════════════════════════════════
    #  ANALISE COMPLETA
    # ══════════════════════════════════════════════

    def get_full_analysis(self, symbol: str) -> dict:
        errors = []

        candles_1h  = self.hl.get_candles(symbol, "1h",  100)
        candles_4h  = self.hl.get_candles(symbol, "4h",  60)
        candles_15m = self.hl.get_candles(symbol, "15m", 100)
        candles_1d  = self.hl.get_candles(symbol, "1d",  30)

        if not candles_1h:  errors.append("candles_1h indisponivel")
        if not candles_4h:  errors.append("candles_4h indisponivel")
        if not candles_15m: errors.append("candles_15m indisponivel")

        funding    = self.hl.get_funding_rate(symbol)
        book       = self.hl.get_l2_book(symbol)
        trades     = self.hl.get_recent_trades(symbol)
        mark_price = self.hl.get_mark_price(symbol)

        if not book:        errors.append("orderbook indisponivel")
        if mark_price == 0: errors.append("mark_price=0 — possivel erro de API")

        ta_15m = self._calculate_indicators(candles_15m) if candles_15m else {}
        ta_1h  = self._calculate_indicators(candles_1h)  if candles_1h  else {}
        ta_4h  = self._calculate_indicators(candles_4h)  if candles_4h  else {}
        ta_1d  = self._calculate_indicators(candles_1d)  if candles_1d  else {}

        structure = self._analyze_market_structure(candles_4h) if candles_4h else {}
        patterns  = self._detect_patterns(candles_1h)          if candles_1h else {}
        volume    = self._analyze_volume(candles_1h)            if candles_1h else {}
        book_ana  = self._analyze_orderbook(book)
        macro     = self._get_macro_context()

        funding_data = {}
        if funding:
            fr = funding.get("funding_rate", 0)
            funding_data = {
                "rate":         fr,
                "annual_pct":   round(fr * 3 * 365 * 100, 2),
                "open_interest": funding.get("open_interest", 0),
                "sentiment":    "bearish_bias" if fr > 0.0005 else
                                "bullish_bias" if fr < -0.0005 else "neutral",
            }

        return {
            "symbol":          symbol,
            "mark_price":      mark_price,
            "data_errors":     errors,
            "macro_sentiment": macro,
            "funding":         funding_data,
            "indicators": {
                "15m": ta_15m,
                "1h":  ta_1h,
                "4h":  ta_4h,
                "1d":  ta_1d,
            },
            "market_structure":      structure,
            "patterns":              patterns,
            "volume":                volume,
            "orderbook":             book_ana,
            "recent_trades_summary": self._summarize_trades(trades),
        }

    # ══════════════════════════════════════════════
    #  V1 (4h/1h) — leve, previsível, pronto para execução
    # ══════════════════════════════════════════════

    def get_v1_analysis(self, symbol: str) -> dict:
        """Análise V1 focada em 4h (tendência) + 1h (gatilho).

        Por padrão NÃO inclui macro/orderbook/estrutura avançada (deixa o bot mais estável).
        Pode ser habilitado via flags em config (ENABLE_*).
        """
        errors = []

        candles_1h = self.hl.get_candles(symbol, "1h", 200)
        candles_4h = self.hl.get_candles(symbol, "4h", 200)

        if not candles_1h:
            errors.append("candles_1h indisponivel")
        if not candles_4h:
            errors.append("candles_4h indisponivel")

        mark_price = self.hl.get_mark_price(symbol)
        if mark_price == 0:
            errors.append("mark_price=0 — possivel erro de API")

        tf_1h = self._calculate_v1_indicators(candles_1h) if candles_1h else {}
        tf_4h = self._calculate_v1_indicators(candles_4h) if candles_4h else {}

        # Tendência 4h (filtro): baseado em preço vs EMA50/EMA200 + inclinação da EMA50
        trend_4h = self._classify_trend_4h(candles_4h) if candles_4h else {"bias": "UNKNOWN"}

        # Sugerir distância de stop em ATR (1h). O LLM ainda escolhe níveis, mas isso guia.
        atr = float(tf_1h.get("atr", 0) or 0)
        stop_atr_mult = 1.2
        suggested_sl_distance = atr * stop_atr_mult if atr > 0 else 0

        payload = {
            "symbol": symbol,
            "style": "4h_1h",
            "mark_price": mark_price,
            "data_errors": errors,
            "tf": {
                "4h": {**tf_4h, **trend_4h},
                "1h": tf_1h,
            },
            "suggestions": {
                "stop_atr_mult": stop_atr_mult,
                "suggested_sl_distance": suggested_sl_distance,
            },
        }

        # Extras opcionais
        if _config and getattr(_config, "ENABLE_MACRO", False):
            payload["macro_sentiment"] = self._get_macro_context()

        if _config and getattr(_config, "ENABLE_ORDERBOOK", False):
            book = self.hl.get_l2_book(symbol)
            payload["orderbook"] = self._analyze_orderbook(book) if book else None

        if _config and getattr(_config, "ENABLE_STRUCTURE", False):
            payload["market_structure"] = self._analyze_market_structure(candles_4h) if candles_4h else {}

        if _config and getattr(_config, "ENABLE_PATTERNS", False):
            payload["patterns"] = self._detect_patterns(candles_1h) if candles_1h else {}

        return payload

    # ══════════════════════════════════════════════
    #  OHLCV
    # ══════════════════════════════════════════════

    def _extract_ohlcv(self, candles: list) -> tuple:
        opens, highs, lows, closes, volumes = [], [], [], [], []
        for c in candles:
            try:
                if isinstance(c, dict):
                    o  = float(c.get("o", c.get("open",   0)) or 0)
                    h  = float(c.get("h", c.get("high",   0)) or 0)
                    l  = float(c.get("l", c.get("low",    0)) or 0)
                    cl = float(c.get("c", c.get("close",  0)) or 0)
                    v  = float(c.get("v", c.get("volume", 0)) or 0)
                elif isinstance(c, (list, tuple)) and len(c) >= 5:
                    o, h, l, cl, v = float(c[0] or 0), float(c[1] or 0), float(c[2] or 0), float(c[3] or 0), float(c[4] or 0)
                else:
                    continue
                if cl > 0:
                    opens.append(o); highs.append(h); lows.append(l)
                    closes.append(cl); volumes.append(v)
            except Exception:
                continue
        return opens, highs, lows, closes, volumes

    # ══════════════════════════════════════════════
    #  INDICADORES
    # ══════════════════════════════════════════════

    def _calculate_indicators(self, candles: list) -> dict:
        if not candles or len(candles) < 20:
            return {}
        try:
            opens, highs, lows, closes, volumes = self._extract_ohlcv(candles)
            if len(closes) < 20:
                return {}

            price = closes[-1]
            ema8   = self._ema(closes, 8)
            ema21  = self._ema(closes, 21)
            ema50  = self._ema(closes, 50)
            ema200 = self._ema(closes, 200) if len(closes) >= 200 else closes[-1]
            rsi    = self._rsi(closes, 14)
            macd_l, macd_s, macd_h = self._macd(closes)
            bb_u, bb_m, bb_l = self._bollinger(closes, 20, 2)
            atr    = self._atr(highs, lows, closes, 14)
            atr_pct = (atr / price * 100) if price > 0 else 0
            sk, sd  = self._stochastic(highs, lows, closes, 14, 3)
            vwap    = self._vwap(highs, lows, closes, volumes)

            vol_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else (sum(volumes) / len(volumes) if volumes else 1)
            vol_cur = volumes[-1] if volumes else 0
            vol_ratio = vol_cur / vol_avg if vol_avg > 0 else 1

            bb_pos = ((price - bb_l) / (bb_u - bb_l) * 100) if (bb_u - bb_l) > 0 else 50

            if ema8 > ema21 > ema50:   trend = "UPTREND"
            elif ema8 < ema21 < ema50: trend = "DOWNTREND"
            else:                      trend = "SIDEWAYS"

            return {
                "price": {
                    "current":    price,
                    "high_24h":   max(highs[-24:])  if len(highs)  >= 24 else max(highs),
                    "low_24h":    min(lows[-24:])   if len(lows)   >= 24 else min(lows),
                    "change_24h": round(((price - closes[-25]) / closes[-25] * 100), 2) if len(closes) > 24 else 0,
                },
                "trend": {
                    "direction":     trend,
                    "ema8":          round(ema8,   4),
                    "ema21":         round(ema21,  4),
                    "ema50":         round(ema50,  4),
                    "ema200":        round(ema200, 4),
                    "price_vs_ema50":  "above" if price > ema50  else "below",
                    "price_vs_ema200": "above" if price > ema200 else "below",
                    "price_vs_vwap":   "above" if price > vwap   else "below",
                    "vwap":          round(vwap, 4),
                },
                "momentum": {
                    "rsi":            round(rsi, 2),
                    "rsi_zone":       "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral",
                    "macd":           round(macd_l, 6),
                    "macd_signal":    round(macd_s, 6),
                    "macd_hist":      round(macd_h, 6),
                    "macd_bullish":   macd_h > 0,
                    "stoch_k":        round(sk, 2),
                    "stoch_d":        round(sd, 2),
                    "stoch_zone":     "overbought" if sk > 80 else "oversold" if sk < 20 else "neutral",
                },
                "volatility": {
                    "atr":            round(atr, 4),
                    "atr_pct":        round(atr_pct, 3),
                    "bb_upper":       round(bb_u, 4),
                    "bb_mid":         round(bb_m, 4),
                    "bb_lower":       round(bb_l, 4),
                    "bb_position_pct": round(bb_pos, 1),
                    "bb_squeeze":     atr_pct < 2.0,
                },
                "volume": {
                    "current":       round(vol_cur, 2),
                    "average_20":    round(vol_avg, 2),
                    "ratio":         round(vol_ratio, 2),
                    "above_average": vol_ratio > 1.5,
                },
            }
        except Exception as e:
            return {"error": str(e)}

    # ══════════════════════════════════════════════
    #  ESTRUTURA DE MERCADO
    # ══════════════════════════════════════════════

    def _analyze_market_structure(self, candles_4h: list) -> dict:
        if not candles_4h or len(candles_4h) < 10:
            return {}
        try:
            _, highs, lows, closes, _ = self._extract_ohlcv(candles_4h)
            swing_highs, swing_lows = [], []
            for i in range(2, len(highs) - 2):
                if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                    swing_highs.append({"index": i, "price": highs[i]})
                if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                    swing_lows.append({"index": i, "price": lows[i]})

            last_sh = swing_highs[-1] if swing_highs else None
            last_sl = swing_lows[-1]  if swing_lows  else None
            prev_sh = swing_highs[-2] if len(swing_highs) > 1 else None
            prev_sl = swing_lows[-2]  if len(swing_lows)  > 1 else None

            bos_bull = bool(last_sh and prev_sh and last_sh["price"] > prev_sh["price"])
            bos_bear = bool(last_sl and prev_sl and last_sl["price"] < prev_sl["price"])

            if bos_bull and not bos_bear:   structure = "BULLISH_HH_HL"
            elif bos_bear and not bos_bull: structure = "BEARISH_LH_LL"
            else:                           structure = "RANGING"

            return {
                "structure":        structure,
                "bos_bullish":      bos_bull,
                "bos_bearish":      bos_bear,
                "last_swing_high":  round(last_sh["price"], 4) if last_sh else None,
                "last_swing_low":   round(last_sl["price"], 4) if last_sl else None,
                "resistance_zones": [round(s["price"], 2) for s in swing_highs[-5:]],
                "support_zones":    [round(s["price"], 2) for s in swing_lows[-5:]],
            }
        except Exception as e:
            return {"error": str(e)}

    # ══════════════════════════════════════════════
    #  PADROES DE CANDLE
    # ══════════════════════════════════════════════

    def _detect_patterns(self, candles: list) -> dict:
        if not candles or len(candles) < 5:
            return {}
        try:
            opens, highs, lows, closes, _ = self._extract_ohlcv(candles)
            if len(closes) < 3:
                return {}
            patterns = []
            c, o, h, l = closes[-1], opens[-1], highs[-1], lows[-1]
            c1, o1     = closes[-2], opens[-2]
            body       = abs(c - o)
            rng        = h - l
            upper_wick = h - max(c, o)
            lower_wick = min(c, o) - l

            if rng > 0:
                if body / rng < 0.1:                                         patterns.append("DOJI")
                if lower_wick > body * 2 and upper_wick < body * 0.5 and c > o: patterns.append("HAMMER_BULLISH")
                if upper_wick > body * 2 and lower_wick < body * 0.5 and c < o: patterns.append("SHOOTING_STAR_BEARISH")
                if body / rng > 0.9:
                    patterns.append("BULLISH_MARUBOZU" if c > o else "BEARISH_MARUBOZU")
                if lower_wick > rng * 0.6: patterns.append("PIN_BAR_BULLISH")
                if upper_wick > rng * 0.6: patterns.append("PIN_BAR_BEARISH")

            if c > o and c1 < o1 and c > o1 and o < c1: patterns.append("BULLISH_ENGULFING")
            if c < o and c1 > o1 and c < o1 and o > c1: patterns.append("BEARISH_ENGULFING")
            if h < highs[-2] and l > lows[-2]:            patterns.append("INSIDE_BAR")

            return {
                "detected":    patterns,
                "last_candle": {
                    "bullish":         c > o,
                    "body_pct":        round(body / rng * 100, 1) if rng > 0 else 0,
                    "upper_wick_pct":  round(upper_wick / rng * 100, 1) if rng > 0 else 0,
                    "lower_wick_pct":  round(lower_wick / rng * 100, 1) if rng > 0 else 0,
                }
            }
        except Exception as e:
            return {"error": str(e)}

    # ══════════════════════════════════════════════
    #  ORDERBOOK
    # ══════════════════════════════════════════════

    def _analyze_orderbook(self, book: dict) -> dict:
        if not book or not isinstance(book, dict):
            return {}
        levels = book.get("levels", [[], []])
        if not levels or len(levels) < 2:
            return {}
        bids, asks = levels[0], levels[1]
        if not bids or not asks:
            return {}
        try:
            best_bid   = float(bids[0][0]) if bids else 0
            best_ask   = float(asks[0][0]) if asks else 0
            spread     = best_ask - best_bid
            spread_pct = (spread / best_bid * 100) if best_bid > 0 else 0
            bid_vol    = sum(float(b[1]) for b in bids[:10])
            ask_vol    = sum(float(a[1]) for a in asks[:10])
            imbalance  = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
            return {
                "best_bid":         round(best_bid, 4),
                "best_ask":         round(best_ask, 4),
                "spread_pct":       round(spread_pct, 4),
                "bid_vol_top10":    round(bid_vol, 2),
                "ask_vol_top10":    round(ask_vol, 2),
                "imbalance":        round(imbalance, 4),
                "pressure":         "BUY" if imbalance > 0.2 else "SELL" if imbalance < -0.2 else "BALANCED",
            }
        except Exception:
            return {}

    # ══════════════════════════════════════════════
    #  Indicadores V1 (4h/1h) — mínimo necessário
    # ══════════════════════════════════════════════

    def _calculate_v1_indicators(self, candles: list) -> dict:
        """Cálculo leve para V1.

        Retorna apenas o que o bot realmente usa no V1: EMA, RSI, ATR e volume ratio.
        """
        if not candles or len(candles) < 60:
            return {}
        try:
            _, highs, lows, closes, volumes = self._extract_ohlcv(candles)
            if len(closes) < 60:
                return {}

            price = float(closes[-1])
            ema21 = self._ema(closes, 21)
            ema50 = self._ema(closes, 50)
            ema200 = self._ema(closes, 200) if len(closes) >= 200 else self._ema(closes, 120)
            rsi14 = self._rsi(closes, 14)
            atr14 = self._atr(highs, lows, closes, 14)
            atr_pct = (atr14 / price * 100) if price > 0 else 0

            vol_ratio = 1.0
            if volumes and len(volumes) >= 20:
                vol_avg = sum(volumes[-20:]) / 20
                vol_cur = volumes[-1]
                vol_ratio = (vol_cur / vol_avg) if vol_avg > 0 else 1.0

            return {
                "price": round(price, 6),
                "ema21": round(float(ema21), 6),
                "ema50": round(float(ema50), 6),
                "ema200": round(float(ema200), 6),
                "rsi14": round(float(rsi14), 2),
                "atr": round(float(atr14), 6),
                "atr_pct": round(float(atr_pct), 3),
                "vol_ratio": round(float(vol_ratio), 3),
            }
        except Exception:
            return {}

    def _classify_trend_4h(self, candles_4h: list) -> dict:
        """Classifica tendência no 4h (filtro) de forma previsível.

        Bias:
          - UPTREND: preço acima EMA50 e EMA50 acima EMA200 e EMA50 subindo
          - DOWNTREND: preço abaixo EMA50 e EMA50 abaixo EMA200 e EMA50 caindo
          - SIDEWAYS: caso contrário
        """
        if not candles_4h or len(candles_4h) < 80:
            return {"bias": "UNKNOWN"}
        try:
            _, _, _, closes, _ = self._extract_ohlcv(candles_4h)
            if len(closes) < 80:
                return {"bias": "UNKNOWN"}

            price = float(closes[-1])
            ema50_now = float(self._ema(closes, 50))
            ema200_now = float(self._ema(closes, 200)) if len(closes) >= 200 else float(self._ema(closes, 120))

            # inclinação aproximada (EMA50 agora vs EMA50 5 candles atrás)
            ema50_prev = float(self._ema(closes[:-5], 50)) if len(closes) > 55 else ema50_now
            slope = "up" if ema50_now > ema50_prev else "down" if ema50_now < ema50_prev else "flat"

            if price > ema50_now and ema50_now > ema200_now and slope == "up":
                bias = "UPTREND"
            elif price < ema50_now and ema50_now < ema200_now and slope == "down":
                bias = "DOWNTREND"
            else:
                bias = "SIDEWAYS"

            return {
                "bias": bias,
                "ema50_slope": slope,
            }
        except Exception:
            return {"bias": "UNKNOWN"}

    # ══════════════════════════════════════════════
    #  VOLUME
    # ══════════════════════════════════════════════

    def _analyze_volume(self, candles: list) -> dict:
        if not candles or len(candles) < 10:
            return {}
        try:
            _, highs, lows, closes, volumes = self._extract_ohlcv(candles)
            vwap     = self._vwap(highs, lows, closes, volumes)
            avg_vol  = sum(volumes) / len(volumes) if volumes else 1
            vol_climax = max(volumes[-20:]) > avg_vol * 3 if len(volumes) >= 20 else False
            return {
                "vwap":          round(vwap, 4),
                "price_vs_vwap": "above" if closes[-1] > vwap else "below",
                "avg_volume":    round(avg_vol, 2),
                "vol_climax":    vol_climax,
                "trend":         "increasing" if volumes[-1] > volumes[-5] else "decreasing",
            }
        except Exception:
            return {}

    def _summarize_trades(self, trades: list) -> dict:
        if not trades:
            return {}
        buy_v  = sum(float(t.get("sz", 0)) for t in trades if t.get("side") == "B")
        sell_v = sum(float(t.get("sz", 0)) for t in trades if t.get("side") == "A")
        total  = buy_v + sell_v
        return {
            "total":        len(trades),
            "buy_volume":   round(buy_v,  4),
            "sell_volume":  round(sell_v, 4),
            "buy_pressure": round(buy_v / total * 100, 1) if total > 0 else 50,
        }

    # ══════════════════════════════════════════════
    #  INDICADORES TECNICOS
    # ══════════════════════════════════════════════

    def _ema(self, data: list, period: int) -> float:
        if len(data) < period:
            return data[-1] if data else 0
        k   = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for p in data[period:]:
            ema = p * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(d, 0)   for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        ag = sum(gains[:period])  / period
        al = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            ag = (ag * (period - 1) + gains[i])  / period
            al = (al * (period - 1) + losses[i]) / period
        if al == 0:
            return 100.0
        return 100 - (100 / (1 + ag / al))

    def _macd(self, closes: list, fast=12, slow=26, signal=9) -> tuple:
        if len(closes) < slow:
            return 0, 0, 0
        macd_values = []
        for i in range(slow - 1, len(closes)):
            ef = self._ema(closes[:i+1], fast)
            es = self._ema(closes[:i+1], slow)
            macd_values.append(ef - es)
        macd_l = macd_values[-1] if macd_values else 0
        macd_s = self._ema(macd_values, signal) if len(macd_values) >= signal else (macd_values[-1] if macd_values else 0)
        return macd_l, macd_s, macd_l - macd_s

    def _bollinger(self, closes: list, period=20, std_dev=2) -> tuple:
        if len(closes) < period:
            c = closes[-1]
            return c, c, c
        recent = closes[-period:]
        sma    = sum(recent) / period
        var    = sum((x - sma) ** 2 for x in recent) / period
        std    = math.sqrt(var)
        return sma + std_dev * std, sma, sma - std_dev * std

    def _atr(self, highs: list, lows: list, closes: list, period=14) -> float:
        if len(highs) < period + 1:
            return 0
        trs = []
        for i in range(1, len(highs)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 0
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    def _stochastic(self, highs: list, lows: list, closes: list, k=14, d=3) -> tuple:
        if len(closes) < k:
            return 50, 50
        ks = []
        for i in range(k - 1, len(closes)):
            hi = max(highs[i - k + 1:i + 1])
            lo = min(lows[i  - k + 1:i + 1])
            ks.append(((closes[i] - lo) / (hi - lo) * 100) if hi != lo else 50)
        kv = ks[-1]
        dv = sum(ks[-d:]) / min(d, len(ks))
        return kv, dv

    def _vwap(self, highs: list, lows: list, closes: list, volumes: list) -> float:
        tp     = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        total_v = sum(volumes)
        if total_v == 0:
            return closes[-1] if closes else 0
        return sum(t * v for t, v in zip(tp, volumes)) / total_v
