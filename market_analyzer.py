"""
Market Analyzer
Análise técnica completa com múltiplos indicadores e frameworks
"""

import math
import urllib.request
import json as _json
from typing import Optional


class MarketAnalyzer:
    """Analisador técnico completo de mercado"""
    
    def __init__(self, hl_client):
        self.hl              = hl_client
        self._macro_cache      = None
        self._macro_cache_time = 0  # cache de 1h — F&G nao muda a cada analise
    
    def _get_macro_context(self) -> dict:
        """Busca Fear & Greed Index + BTC Dominance — cache de 1h para economizar APIs"""
        import time as _time
        now = _time.time()

        # Retorna cache se ainda valido (1 hora)
        if self._macro_cache and (now - self._macro_cache_time) < 3600:
            return self._macro_cache

        result = {
            "fear_greed_index":      None,
            "fear_greed_label":      "DESCONHECIDO",
            "fear_greed_warning":    "",
            "btc_dominance":         None,
            "btc_dominance_trend":   "desconhecido",
            "btc_dominance_warning": "",
            "data_source":           "alternative.me + coingecko"
        }

        # ── Fear & Greed ──────────────────────────────────────────────────
        try:
            req  = urllib.request.urlopen("https://api.alternative.me/fng/?limit=2&format=json", timeout=5)
            data = _json.loads(req.read().decode())
            fng  = data["data"]
            val  = int(fng[0]["value"])
            lbl  = fng[0]["value_classification"]
            result["fear_greed_index"] = val
            result["fear_greed_label"] = lbl

            if val <= 20:
                result["fear_greed_warning"] = (
                    "MEDO EXTREMO F&G=" + str(val) + ": PROIBIDO LONG confianca>60%. "
                    "Minimo 5 confluencias tecnicas obrigatorias. Alto risco de queda continuada."
                )
            elif val <= 40:
                result["fear_greed_warning"] = (
                    "MEDO F&G=" + str(val) + ": LONG exige 4+ confluencias. Reduza confianca em 15 pontos."
                )
            elif val >= 80:
                result["fear_greed_warning"] = (
                    "GANANCIA EXTREMA F&G=" + str(val) + ": PROIBIDO LONG confianca>60%. "
                    "Mercado proximo de topo. SHORT tem maior probabilidade."
                )
            elif val >= 60:
                result["fear_greed_warning"] = (
                    "GANANCIA F&G=" + str(val) + ": LONG com cautela. Mercado pode estar sobrecomprado."
                )
        except Exception as e:
            result["fear_greed_warning"] = "ERRO FEAR&GREED: " + str(e) + ". Assuma medo — seja conservador."

        # ── BTC Dominance via CoinGecko ───────────────────────────────────
        try:
            req  = urllib.request.urlopen("https://api.coingecko.com/api/v3/global", timeout=5)
            data = _json.loads(req.read().decode())
            dom  = data.get("data", {}).get("market_cap_percentage", {}).get("btc", None)
            if dom is not None:
                dom = round(float(dom), 2)
                result["btc_dominance"] = dom

                if dom > 58:
                    result["btc_dominance_trend"]   = "muito_alta"
                    result["btc_dominance_warning"]  = (
                        "BTC DOMINANCE MUITO ALTA (" + str(dom) + "%): "
                        "Capital concentrado no BTC. Altcoins em risco severo de queda. "
                        "REDUCAO OBRIGATORIA de confianca em LONG de altcoins em 25 pontos. "
                        "Prefira BTC ou WAIT em altcoins."
                    )
                elif dom > 55:
                    result["btc_dominance_trend"]   = "alta"
                    result["btc_dominance_warning"]  = (
                        "BTC DOMINANCE ALTA (" + str(dom) + "%): "
                        "Altcoins perdendo forca relativa ao BTC. "
                        "Reduza confianca em LONG de altcoins em 20 pontos."
                    )
                elif dom < 45:
                    result["btc_dominance_trend"]   = "baixa"
                    result["btc_dominance_warning"]  = (
                        "BTC DOMINANCE BAIXA (" + str(dom) + "%): "
                        "Alt season possivel — altcoins com boa liquidez relativa."
                    )
                else:
                    result["btc_dominance_trend"]   = "neutra"
                    result["btc_dominance_warning"]  = (
                        "BTC DOMINANCE NEUTRA (" + str(dom) + "%): sem impacto relevante em altcoins."
                    )
        except Exception as e:
            result["btc_dominance_warning"] = "ERRO BTC DOMINANCE: " + str(e) + ". Assuma dominancia alta — seja conservador em altcoins."

        # Salvar no cache
        self._macro_cache      = result
        self._macro_cache_time = _time.time()
        return result

    def get_full_analysis(self, symbol: str) -> dict:
        """Coleta e processa todos os dados de mercado"""
        errors = []

        # Dados em múltiplos timeframes
        candles_1h  = self.hl.get_candles(symbol, "1h", 100)
        candles_4h  = self.hl.get_candles(symbol, "4h", 50)
        candles_15m = self.hl.get_candles(symbol, "15m", 100)

        if not candles_1h:  errors.append("candles_1h indisponivel da API Hyperliquid")
        if not candles_4h:  errors.append("candles_4h indisponivel da API Hyperliquid")
        if not candles_15m: errors.append("candles_15m indisponivel da API Hyperliquid")

        # Dados de mercado
        funding    = self.hl.get_funding_rate(symbol)
        book       = self.hl.get_l2_book(symbol)
        trades     = self.hl.get_recent_trades(symbol)
        mark_price = self.hl.get_mark_price(symbol)

        if not book:    errors.append("orderbook indisponivel da API Hyperliquid — dado real existe mas API falhou")
        if not funding: errors.append("funding rate indisponivel")
        if mark_price == 0: errors.append("mark_price retornou 0 — possivel erro de API")

        # Calcular indicadores
        ta_1h  = self._calculate_indicators(candles_1h)  if candles_1h  else {}
        ta_4h  = self._calculate_indicators(candles_4h)  if candles_4h  else {}
        ta_15m = self._calculate_indicators(candles_15m) if candles_15m else {}

        # Analise de order book
        book_analysis = self._analyze_orderbook(book)

        # Analise de volume
        volume_profile = self._analyze_volume(candles_1h) if candles_1h else {}

        # Detectar padroes
        patterns = self._detect_patterns(candles_1h) if candles_1h else {}

        # Market structure
        structure = self._analyze_market_structure(candles_4h) if candles_4h else {}

        # Macro context — Fear & Greed
        macro = self._get_macro_context()

        return {
            "symbol": symbol,
            "mark_price": mark_price,
            "data_errors": errors,  # transparencia: erros tecnicos explicados
            "funding": {
                "rate": funding.get("funding_rate", 0) if funding else 0,
                "annual_rate": funding.get("funding_rate", 0) * 3 * 365 * 100 if funding else 0,
                "open_interest": funding.get("open_interest", 0) if funding else 0,
                "sentiment": "bearish_bias" if (funding or {}).get("funding_rate", 0) > 0.0005 else
                             "bullish_bias" if (funding or {}).get("funding_rate", 0) < -0.0005 else "neutral"
            },
            "macro_sentiment": macro,
            "indicators": {
                "15m": ta_15m,
                "1h":  ta_1h,
                "4h":  ta_4h
            },
            "orderbook": book_analysis,
            "volume": volume_profile,
            "patterns": patterns,
            "market_structure": structure,
            "recent_trades_summary": self._summarize_trades(trades)
        }
    
    def _extract_ohlcv(self, candles: list) -> tuple:
        """Extrai arrays OHLCV dos candles — compatível com formato Hyperliquid"""
        opens, highs, lows, closes, volumes = [], [], [], [], []
        for c in candles:
            try:
                if isinstance(c, dict):
                    o  = float(c.get('o', c.get('open',   0)) or 0)
                    h  = float(c.get('h', c.get('high',   0)) or 0)
                    l  = float(c.get('l', c.get('low',    0)) or 0)
                    cl = float(c.get('c', c.get('close',  0)) or 0)
                    v  = float(c.get('v', c.get('volume', 0)) or 0)
                elif isinstance(c, (list, tuple)) and len(c) >= 5:
                    o, h, l, cl, v = (float(c[0] or 0), float(c[1] or 0),
                                      float(c[2] or 0), float(c[3] or 0),
                                      float(c[4] or 0))
                else:
                    continue
                if cl > 0:
                    opens.append(o)
                    highs.append(h)
                    lows.append(l)
                    closes.append(cl)
                    volumes.append(v)
            except Exception:
                continue
        return opens, highs, lows, closes, volumes
    
    def _calculate_indicators(self, candles: list) -> dict:
        """Calcula indicadores técnicos completos"""
        if len(candles) < 20:
            return {}
        
        try:
            opens, highs, lows, closes, volumes = self._extract_ohlcv(candles)
            
            current_price = closes[-1]
            
            # EMAs
            ema8 = self._ema(closes, 8)
            ema21 = self._ema(closes, 21)
            ema50 = self._ema(closes, 50)
            ema200 = self._ema(closes, 200) if len(closes) >= 200 else closes[-1]
            
            # RSI
            rsi = self._rsi(closes, 14)
            
            # MACD
            macd_line, signal_line, histogram = self._macd(closes)
            
            # Bollinger Bands
            bb_upper, bb_mid, bb_lower = self._bollinger_bands(closes, 20, 2)
            
            # ATR
            atr = self._atr(highs, lows, closes, 14)
            atr_pct = (atr / current_price) * 100
            
            # Volume médio
            vol_avg = sum(volumes[-20:]) / 20
            vol_current = volumes[-1]
            vol_ratio = vol_current / vol_avg if vol_avg > 0 else 1
            
            # Stochastic
            stoch_k, stoch_d = self._stochastic(highs, lows, closes, 14, 3)
            
            # Detecção de tendência
            trend = "UPTREND" if ema8 > ema21 > ema50 else \
                    "DOWNTREND" if ema8 < ema21 < ema50 else "SIDEWAYS"
            
            # BB Position
            bb_position = ((current_price - bb_lower) / (bb_upper - bb_lower)) * 100 if bb_upper != bb_lower else 50
            
            return {
                "price": {
                    "current": current_price,
                    "high_24h": max(highs[-24:]) if len(highs) >= 24 else max(highs),
                    "low_24h": min(lows[-24:]) if len(lows) >= 24 else min(lows),
                    "change_24h_pct": ((current_price - closes[-25]) / closes[-25] * 100) if len(closes) > 24 else 0
                },
                "trend": {
                    "direction": trend,
                    "ema8": round(ema8, 4),
                    "ema21": round(ema21, 4),
                    "ema50": round(ema50, 4),
                    "ema200": round(ema200, 4),
                    "price_vs_ema200": "above" if current_price > ema200 else "below"
                },
                "momentum": {
                    "rsi": round(rsi, 2),
                    "rsi_signal": "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral",
                    "macd": round(macd_line, 6),
                    "macd_signal": round(signal_line, 6),
                    "macd_histogram": round(histogram, 6),
                    "macd_bullish": histogram > 0,
                    "stoch_k": round(stoch_k, 2),
                    "stoch_d": round(stoch_d, 2),
                    "stoch_signal": "overbought" if stoch_k > 80 else "oversold" if stoch_k < 20 else "neutral"
                },
                "volatility": {
                    "atr": round(atr, 4),
                    "atr_pct": round(atr_pct, 3),
                    "bb_upper": round(bb_upper, 4),
                    "bb_mid": round(bb_mid, 4),
                    "bb_lower": round(bb_lower, 4),
                    "bb_position_pct": round(bb_position, 1),
                    "bb_squeeze": atr_pct < 2.0
                },
                "volume": {
                    "current": round(vol_current, 2),
                    "average": round(vol_avg, 2),
                    "ratio": round(vol_ratio, 2),
                    "above_average": vol_ratio > 1.5
                }
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _analyze_market_structure(self, candles_4h: list) -> dict:
        """Analisa estrutura de mercado: swing highs/lows, BOS, CHoCH"""
        if len(candles_4h) < 10:
            return {}
        
        _, highs, lows, closes, _ = self._extract_ohlcv(candles_4h)
        
        # Identificar swing highs e lows
        swing_highs = []
        swing_lows = []
        
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
               highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                swing_highs.append({"index": i, "price": highs[i]})
            
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
               lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                swing_lows.append({"index": i, "price": lows[i]})
        
        # Últimos swing points
        last_sh = swing_highs[-1] if swing_highs else None
        last_sl = swing_lows[-1] if swing_lows else None
        prev_sh = swing_highs[-2] if len(swing_highs) > 1 else None
        prev_sl = swing_lows[-2] if len(swing_lows) > 1 else None
        
        # Detectar BOS (Break of Structure)
        bos_bullish = False
        bos_bearish = False
        
        if last_sh and prev_sh:
            bos_bullish = last_sh["price"] > prev_sh["price"]
        if last_sl and prev_sl:
            bos_bearish = last_sl["price"] < prev_sl["price"]
        
        # Determinar estrutura
        if bos_bullish and not bos_bearish:
            structure = "BULLISH (HH/HL)"
        elif bos_bearish and not bos_bullish:
            structure = "BEARISH (LH/LL)"
        else:
            structure = "RANGING/TRANSITION"
        
        # Zonas de liquidez
        resistance_zones = [sh["price"] for sh in swing_highs[-5:]] if swing_highs else []
        support_zones = [sl["price"] for sl in swing_lows[-5:]] if swing_lows else []
        
        return {
            "structure": structure,
            "bos_bullish": bos_bullish,
            "bos_bearish": bos_bearish,
            "last_swing_high": last_sh["price"] if last_sh else None,
            "last_swing_low": last_sl["price"] if last_sl else None,
            "resistance_zones": [round(r, 2) for r in resistance_zones],
            "support_zones": [round(s, 2) for s in support_zones],
        }
    
    def _detect_patterns(self, candles: list) -> dict:
        """Detecta padrões de candlestick e gráficos"""
        if len(candles) < 5:
            return {}
        
        opens, highs, lows, closes, _ = self._extract_ohlcv(candles)
        
        patterns = []
        
        # Últimos candles
        c = closes[-1]; o = opens[-1]; h = highs[-1]; l = lows[-1]
        c1 = closes[-2]; o1 = opens[-2]; h1 = highs[-2]; l1 = lows[-2]
        c2 = closes[-3]; o2 = opens[-3]
        
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l
        candle_range = h - l
        
        # Doji
        if candle_range > 0 and body / candle_range < 0.1:
            patterns.append("DOJI")
        
        # Hammer
        if lower_wick > body * 2 and upper_wick < body * 0.5 and c > o:
            patterns.append("HAMMER_BULLISH")
        
        # Shooting Star
        if upper_wick > body * 2 and lower_wick < body * 0.5 and c < o:
            patterns.append("SHOOTING_STAR_BEARISH")
        
        # Engulfing
        if c > o and c1 < o1 and c > o1 and o < c1:
            patterns.append("BULLISH_ENGULFING")
        elif c < o and c1 > o1 and c < o1 and o > c1:
            patterns.append("BEARISH_ENGULFING")
        
        # Marubozu
        if candle_range > 0 and body / candle_range > 0.9:
            if c > o:
                patterns.append("BULLISH_MARUBOZU")
            else:
                patterns.append("BEARISH_MARUBOZU")
        
        # Inside Bar
        if h < h1 and l > l1:
            patterns.append("INSIDE_BAR_COMPRESSION")
        
        # Pin Bar
        if lower_wick > candle_range * 0.6:
            patterns.append("PIN_BAR_BULLISH_REVERSAL")
        elif upper_wick > candle_range * 0.6:
            patterns.append("PIN_BAR_BEARISH_REVERSAL")
        
        return {
            "detected": patterns,
            "last_candle": {
                "bullish": c > o,
                "body_pct": round(body / candle_range * 100, 1) if candle_range > 0 else 0,
                "upper_wick_pct": round(upper_wick / candle_range * 100, 1) if candle_range > 0 else 0,
                "lower_wick_pct": round(lower_wick / candle_range * 100, 1) if candle_range > 0 else 0,
            }
        }
    
    def _analyze_orderbook(self, book: dict) -> dict:
        """Analisa o livro de ordens para detectar liquidez"""
        if not book or not isinstance(book, dict):
            return {}
        levels = book.get("levels", [[], []])
        if not levels or len(levels) < 2:
            return {}
        
        bids = levels[0]  # [[price, size], ...]
        asks = levels[1]
        
        if not bids or not asks:
            return {}
        
        try:
            best_bid = float(bids[0][0]) if bids else 0
            best_ask = float(asks[0][0]) if asks else 0
            spread = best_ask - best_bid
            spread_pct = (spread / best_bid * 100) if best_bid > 0 else 0
            
            # Volume nos top 10 níveis
            bid_volume = sum(float(b[1]) for b in bids[:10])
            ask_volume = sum(float(a[1]) for a in asks[:10])
            
            imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume) if (bid_volume + ask_volume) > 0 else 0
            
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": round(spread, 4),
                "spread_pct": round(spread_pct, 4),
                "bid_volume_top10": round(bid_volume, 2),
                "ask_volume_top10": round(ask_volume, 2),
                "imbalance": round(imbalance, 4),
                "pressure": "BUY_PRESSURE" if imbalance > 0.2 else "SELL_PRESSURE" if imbalance < -0.2 else "BALANCED"
            }
        except:
            return {}
    
    def _analyze_volume(self, candles: list) -> dict:
        """Analisa perfil de volume"""
        if not candles or len(candles) < 10:
            return {}
        
        _, highs, lows, closes, volumes = self._extract_ohlcv(candles)
        
        # VWAP
        vwap = self._calculate_vwap(highs, lows, closes, volumes)
        
        # Volume por sessão (últimas 24h = 24 candles de 1h)
        recent_vol = sum(volumes[-24:]) if len(volumes) >= 24 else sum(volumes)
        avg_vol = sum(volumes) / len(volumes)
        
        # Detectar climax de volume
        max_vol = max(volumes[-20:])
        vol_climax = max_vol > avg_vol * 3
        
        return {
            "vwap": round(vwap, 4),
            "price_vs_vwap": "above" if closes[-1] > vwap else "below",
            "recent_24h_volume": round(recent_vol, 2),
            "average_volume": round(avg_vol, 2),
            "volume_climax": vol_climax,
            "trend": "increasing" if volumes[-1] > volumes[-5] else "decreasing"
        }
    
    def _summarize_trades(self, trades: list) -> dict:
        """Resume os trades recentes"""
        if not trades:
            return {}
        
        buy_vol = sum(float(t.get("sz", 0)) for t in trades if t.get("side") == "B")
        sell_vol = sum(float(t.get("sz", 0)) for t in trades if t.get("side") == "A")
        
        return {
            "total_trades": len(trades),
            "buy_volume": round(buy_vol, 4),
            "sell_volume": round(sell_vol, 4),
            "buy_pressure": buy_vol / (buy_vol + sell_vol) * 100 if (buy_vol + sell_vol) > 0 else 50
        }
    
    # ══════════════════════════════════════════════
    #  INDICADORES TÉCNICOS
    # ══════════════════════════════════════════════
    
    def _ema(self, data: list, period: int) -> float:
        """Exponential Moving Average"""
        if len(data) < period:
            return data[-1] if data else 0
        
        k = 2 / (period + 1)
        ema = sum(data[:period]) / period
        
        for price in data[period:]:
            ema = price * k + ema * (1 - k)
        
        return ema
    
    def _rsi(self, closes: list, period: int = 14) -> float:
        """Relative Strength Index"""
        if len(closes) < period + 1:
            return 50.0
        
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _macd(self, closes: list, fast=12, slow=26, signal=9) -> tuple:
        """MACD Line, Signal Line, Histogram"""
        if len(closes) < slow:
            return 0, 0, 0
        
        ema_fast = self._ema(closes, fast)
        ema_slow = self._ema(closes, slow)
        macd_line = ema_fast - ema_slow
        
        # Signal line (EMA do MACD)
        macd_values = []
        for i in range(slow - 1, len(closes)):
            ef = self._ema(closes[:i+1], fast)
            es = self._ema(closes[:i+1], slow)
            macd_values.append(ef - es)
        
        signal_line = self._ema(macd_values, signal) if len(macd_values) >= signal else macd_values[-1] if macd_values else 0
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    def _bollinger_bands(self, closes: list, period=20, std_dev=2) -> tuple:
        """Bollinger Bands: upper, middle, lower"""
        if len(closes) < period:
            c = closes[-1]
            return c, c, c
        
        recent = closes[-period:]
        sma = sum(recent) / period
        variance = sum((x - sma) ** 2 for x in recent) / period
        std = math.sqrt(variance)
        
        return sma + std_dev * std, sma, sma - std_dev * std
    
    def _atr(self, highs: list, lows: list, closes: list, period=14) -> float:
        """Average True Range"""
        if len(highs) < period + 1:
            return 0
        
        true_ranges = []
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            true_ranges.append(tr)
        
        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges)
        
        atr = sum(true_ranges[:period]) / period
        for tr in true_ranges[period:]:
            atr = (atr * (period - 1) + tr) / period
        
        return atr
    
    def _stochastic(self, highs: list, lows: list, closes: list, k_period=14, d_period=3) -> tuple:
        """Stochastic Oscillator %K and %D"""
        if len(closes) < k_period:
            return 50, 50
        
        k_values = []
        for i in range(k_period - 1, len(closes)):
            highest = max(highs[i - k_period + 1:i + 1])
            lowest = min(lows[i - k_period + 1:i + 1])
            if highest != lowest:
                k = ((closes[i] - lowest) / (highest - lowest)) * 100
            else:
                k = 50
            k_values.append(k)
        
        k = k_values[-1]
        d = sum(k_values[-d_period:]) / min(d_period, len(k_values))
        
        return k, d
    
    def _calculate_vwap(self, highs: list, lows: list, closes: list, volumes: list) -> float:
        """Volume Weighted Average Price"""
        typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        
        total_pv = sum(tp * v for tp, v in zip(typical_prices, volumes))
        total_v = sum(volumes)
        
        return total_pv / total_v if total_v > 0 else closes[-1]
