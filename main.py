#!/usr/bin/env python3
"""HyperTrader AI — Hyperliquid Perpetuals | Claude AI"""
import os, re, json, asyncio, logging, time
from datetime import datetime, timezone
from typing import Optional
import anthropic
from dotenv import load_dotenv
load_dotenv()
from hyperliquid_client import HyperliquidClient
from market_analyzer import MarketAnalyzer
from risk_manager import RiskManager

os.makedirs("logs", exist_ok=True)
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("logs/hypertrader.log"), logging.StreamHandler()])
logger = logging.getLogger("HyperTrader")
STATE_FILE = "state.json"

CONFIG = {
    "anthropic_api_key":   os.getenv("ANTHROPIC_API_KEY",""),
    "hl_wallet_address":   os.getenv("HL_WALLET_ADDRESS",""),
    "hl_private_key":      os.getenv("HL_PRIVATE_KEY",""),
    "symbols":             ["BTC","ETH","SOL","AVAX"],
    "trading_allocation":  1.0,
    "max_positions":       4,
    "risk_per_trade":      0.02,
    "max_trades_day":      6,
    "daily_loss_limit":    0.06,
    "drawdown_half":       10.0,
    "drawdown_stop":       20.0,
    "leverage_map":        {"BTC":5,"ETH":5,"SOL":3,"AVAX":3,"PEPE":2},
    "max_leverage":        10,
    "leverage_fallback":   3,
    "min_confidence":      60,
    "take_profit_ratio":   1.5,
    "partial_tp_pct":      0.50,
    "trailing_stop":       True,
    "market_order_threshold": 0.3,
    "analysis_interval":   1800,
    "pnl_update_interval": 120,
    "use_testnet":         os.getenv("USE_TESTNET","true").lower()=="true",
    "dry_run":             os.getenv("DRY_RUN","true").lower()=="true",
    "telegram_token":      os.getenv("TELEGRAM_BOT_TOKEN",""),
    "telegram_chat_id":    int(os.getenv("TELEGRAM_CHAT_ID","0")),
    "telegram_allowed_ids":os.getenv("TELEGRAM_ALLOWED_IDS","0"),
}

TRADER_SYSTEM_PROMPT = """Trader profissional de perpetuals na Hyperliquid. Capital real em jogo.

FILOSOFIA: Um trader profissional adapta a DIRECAO ao mercado. NUNCA para de operar.
Mercado em queda? Busca SHORTs. Mercado em alta? Busca LONGs. Medo Extremo = SHORT, nao WAIT.

PASSO 1 — BIAS MACRO (adapta, nao bloqueia):
  F&G  0-25  Medo Extremo:   SHORT fortemente favorecido. LONG so em suporte + reversao confirmada. LONG -20pts, SHORT +10pts.
  F&G 25-45  Medo:           SHORT levemente favorecido. LONG -10pts.
  F&G 45-55  Neutro:         Tecnica pura.
  F&G 55-75  Ganancia:       LONG levemente favorecido.
  F&G 75-100 Ganancia Ext:   SHORT favorecido. LONG -15pts.
  BTC Dom > 55% subindo:     LONG altcoins -15pts.
  data_errors presentes:     cap confianca em 50%.

PASSO 2 — TENDENCIA:
  UPTREND 4h:   preco ACIMA MM50 + MM50 ascendente + HH/HL
  DOWNTREND 4h: preco ABAIXO MM50 + MM50 descendente + LH/LL
  LATERAL:      qualquer outro caso

PASSO 3 — CONFLUENCIAS (minimo 3, 5 para confianca>=75%):
  BOS/CHoCH, order block, FVG, RSI divergencia, volume acima media,
  funding favoravel, suporte/resistencia testado, padrao de vela,
  EMA cross, MACD sinal, Bollinger breakout, VWAP, Stochastic extremo

PASSO 4 — DECISAO:
  LONG:  uptrend + bias favoravel + 3+ confluencias + volume
  SHORT: downtrend + bias neutro/negativo + 3+ confluencias + volume
  WAIT:  lateral sem direcao E menos de 3 confluencias (UNICO caso valido)

PASSO 5 — NIVEIS (REGRA ABSOLUTA):
  LONG:  stop_loss ABAIXO da entrada. tp1 e tp2 ACIMA da entrada. tp2 > tp1.
  SHORT: stop_loss ACIMA da entrada. tp1 e tp2 ABAIXO da entrada. tp2 < tp1.
  R:R minimo 1:2 (tp1 vs sl). NAO inclua leverage no JSON.

RESPONDA APENAS COM JSON puro sem backticks:
{"signal":"LONG","confidence":80,"entry_price":0.0,"stop_loss":0.0,"take_profit_1":0.0,"take_profit_2":0.0,"timeframe":"1h","momento":"resumo em 1 linha","reasoning_tecnico":"analise detalhada","reasoning_simples":"em palavras simples","ganho_maximo":"ex: +8%","perda_maxima":"ex: -2%","invalidation":"quando cancela o sinal"}
"""


class HyperTraderAgent:

    def __init__(self):
        self.ai       = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
        self.hl       = HyperliquidClient(CONFIG["hl_wallet_address"], CONFIG["hl_private_key"], CONFIG["use_testnet"])
        self.analyzer = MarketAnalyzer(self.hl)
        self.risk     = RiskManager(CONFIG)
        self.history: dict           = {}
        self.active_positions: dict  = {}
        self.trade_history: list     = []
        self.daily_pnl               = 0.0
        self.session_pnl             = 0.0
        self.paused                  = False
        self.session_start           = datetime.now(timezone.utc)
        self.telegram_bot            = None
        self.stats                   = {"total":0,"wins":0,"losses":0,"best":0.0,"worst":0.0}
        self.asset_leverage_map:dict = {}
        self._current_day            = datetime.now(timezone.utc).date()
        self.daily_trades_count      = 0
        self._load_state()
        self._load_leverage_limits()
        if not CONFIG["dry_run"]:
            self._sync_with_exchange()
        logger.info(f"HyperTrader iniciado | {CONFIG['symbols']} | {'TESTNET' if CONFIG['use_testnet'] else 'MAINNET'} | {'DRY RUN' if CONFIG['dry_run'] else 'LIVE'}")

    # ── Persistencia

    def _save_state(self):
        try:
            state = {"active_positions":self.active_positions,"trade_history":self.trade_history[-100:],
                     "stats":self.stats,"session_pnl":self.session_pnl,"daily_pnl":self.daily_pnl,
                     "daily_trades_count":self.daily_trades_count,"peak_equity":self.risk.peak_equity,
                     "saved_at":datetime.now(timezone.utc).isoformat()}
            tmp = STATE_FILE+".tmp"
            with open(tmp,"w") as f: json.dump(state,f,indent=2,default=str)
            os.replace(tmp,STATE_FILE)
        except Exception as e: logger.error(f"save_state: {e}")

    def _load_state(self):
        if not os.path.exists(STATE_FILE): return
        try:
            with open(STATE_FILE) as f: s=json.load(f)
            self.active_positions   = s.get("active_positions",{})
            self.trade_history      = s.get("trade_history",[])
            self.stats              = s.get("stats",self.stats)
            self.session_pnl        = s.get("session_pnl",0.0)
            self.daily_pnl          = s.get("daily_pnl",0.0)
            self.daily_trades_count = s.get("daily_trades_count",0)
            self.risk.peak_equity   = s.get("peak_equity",0.0)
            logger.info(f"State: {len(self.active_positions)} posicoes, peak=${self.risk.peak_equity:.2f}")
        except Exception as e: logger.error(f"load_state: {e}")

    # ── Alavancagem fixa

    def _load_leverage_limits(self):
        try:
            info = self.hl.get_asset_info()
            for sym in CONFIG["symbols"]:
                self.asset_leverage_map[sym] = info.get(sym,{}).get("maxLeverage",CONFIG["leverage_fallback"])
        except Exception as e: logger.warning(f"leverage limits: {e}")

    def _sync_with_exchange(self):
        """Sincroniza estado interno com posições reais na Hyperliquid ao iniciar."""
        try:
            logger.info("Sincronizando com a exchange...")
            acc          = self.hl.get_account_info()
            real_pos     = self.hl.get_all_positions()
            open_orders  = self.hl.get_open_orders()
            fills_summary = self.hl.get_fills_summary()
            funding      = self.hl.get_funding_summary()
            equity       = acc.get("equity", 0)

            # Adiciona posições reais que não estão no estado
            for sym, rp in real_pos.items():
                if sym not in self.active_positions:
                    direction = "LONG" if rp["is_long"] else "SHORT"
                    logger.warning(f"Posição real encontrada: {sym} {direction} size={rp['size']}")
                    self.active_positions[sym] = {
                        "direction": direction,
                        "entry":     rp["entry_price"],
                        "stop_loss": 0, "tp1": 0, "tp2": 0,
                        "size":      rp["size"],
                        "leverage":  self.get_leverage_for(sym),
                        "equity_open": equity,
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "peak_pnl":  0.0, "tp1_hit": False, "status": "open",
                        "entry_order_id": None, "sl_order_id": None,
                        "tp1_order_id": None, "tp2_order_id": None,
                        "synced_from_exchange": True,
                        "unrealized_pnl": rp["unrealized_pnl"],
                        "liq_price": rp["liq_price"],
                    }

            # Remove do estado posições que não existem mais na exchange
            for sym in list(self.active_positions.keys()):
                if sym not in real_pos:
                    logger.warning(f"Posição {sym} no estado mas não na exchange — removendo")
                    del self.active_positions[sym]

            self._save_state()

            net = "TESTNET" if CONFIG["use_testnet"] else "MAINNET"
            msg = (f"🔄 <b>Bot Reiniciado — Sincronizado</b>\n────────────────────\n"
                   f"🌐 {net} | {'DRY RUN' if CONFIG['dry_run'] else '🔴 LIVE'}\n"
                   f"💼 Capital: <code>${equity:,.2f}</code>\n"
                   f"📊 Posições abertas: <code>{len(real_pos)}</code>\n"
                   f"📋 Ordens abertas: <code>{len(open_orders)}</code>\n"
                   f"💰 Fills totais: <code>{fills_summary['total_trades']}</code> | "
                   f"Fees: <code>${fills_summary['total_fee']:.4f}</code>\n"
                   f"📈 P&L realizado (fills): <code>${fills_summary['total_pnl']:+.4f}</code>\n"
                   f"💸 Funding 24h: <code>${funding['total_funding_usd']:+.4f}</code>\n")

            if real_pos:
                msg += "────────────────────\n"
                for sym, rp in real_pos.items():
                    em = "🟢" if rp["is_long"] else "🔴"
                    msg += (f"{em} <b>{sym}</b> {'LONG' if rp['is_long'] else 'SHORT'}\n"
                            f"  Entrada: <code>${rp['entry_price']:,.4f}</code> | "
                            f"P&L: <code>${rp['unrealized_pnl']:+.2f}</code> | "
                            f"Liq: <code>${rp['liq_price']:,.4f}</code>\n")

            if open_orders:
                msg += "────────────────────\n📋 <b>Ordens abertas:</b>\n"
                for o in open_orders[:8]:
                    side = "BUY" if o.get("side") == "B" else "SELL"
                    msg += f"  • {o.get('coin')} {side} @ <code>${float(o.get('limitPx',0)):,.4f}</code> sz={o.get('sz')}\n"

            msg += f"\n🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
            self._notify(msg)
            logger.info(f"Sync OK: {len(real_pos)} posicoes, {len(open_orders)} ordens abertas")
        except Exception as e:
            logger.error(f"Erro sync exchange: {e}", exc_info=True)

    def get_leverage_for(self, symbol: str) -> int:
        configured   = CONFIG["leverage_map"].get(symbol, CONFIG["leverage_fallback"])
        platform_max = self.asset_leverage_map.get(symbol, CONFIG["max_leverage"])
        return min(configured, platform_max, CONFIG["max_leverage"])

    # ── LLM

    def _chat(self, prompt: str, ctx: dict = None, symbol: str = "_default") -> str:
        msg  = prompt + ("\n\n" + json.dumps(ctx, indent=2, default=str) if ctx else "")
        hist = self.history.setdefault(symbol, [])
        hist.append({"role":"user","content":msg})
        model = "claude-sonnet-4-6" if symbol in ("_report","_chat") else "claude-haiku-4-5-20251001"
        for attempt in range(5):
            try:
                resp   = self.ai.messages.create(model=model, max_tokens=1024,
                    system=[{"type":"text","text":TRADER_SYSTEM_PROMPT,"cache_control":{"type":"ephemeral"}}],
                    messages=hist)
                answer = resp.content[0].text
                hist.append({"role":"assistant","content":answer})
                if len(hist) > 20: self.history[symbol] = hist[-20:]
                return answer
            except Exception as e:
                err = str(e)
                if "529" in err or "overloaded" in err.lower():
                    wait = 60*(attempt+1)
                    logger.warning(f"API sobrecarregada t{attempt+1}/5, {wait}s")
                    self._notify(f"⏳ API sobrecarregada. Aguardando {wait}s (t{attempt+1}/5)")
                    time.sleep(wait)
                elif "429" in err or "rate" in err.lower():
                    wait = 30*(attempt+1)
                    logger.warning(f"Rate limit t{attempt+1}/5, {wait}s")
                    time.sleep(wait)
                else:
                    logger.error(f"Claude [{symbol}]: {e}")
                    hist.pop()
                    return '{"signal":"WAIT","confidence":0,"reasoning_simples":"Erro API"}'
        hist.pop()
        logger.error(f"API indisponivel 5t [{symbol}]")
        self._notify("🚨 API Claude indisponivel — 5 tentativas. WAIT aplicado.")
        return '{"signal":"WAIT","confidence":0,"reasoning_simples":"API indisponivel"}'

    def _extract_signal(self, text: str) -> Optional[dict]:
        for pat in [r"```json(.*?)```", r"```(.*?)```"]:
            try:
                for m in re.findall(pat, text, re.DOTALL):
                    d = json.loads(m.strip())
                    if "signal" in d: return d
            except: pass
        try:
            d = json.loads(text.strip())
            if "signal" in d: return d
        except: pass
        try:
            s=text.find("{"); e=text.rfind("}")
            if s!=-1 and e>s:
                d=json.loads(text[s:e+1])
                if "signal" in d: return d
        except: pass
        logger.warning(f"Sinal nao extraido: {text[:150]}")
        return None

    # ── Validacao de precos

    def _validate_prices(self, direction:str, entry:float, sl:float, tp1:float, tp2:float) -> tuple:
        if entry<=0 or sl<=0 or tp1<=0:
            return False, f"Precos invalidos: entry={entry} sl={sl} tp1={tp1}"
        if direction=="LONG":
            if sl >= entry: return False, f"LONG: SL {sl:.4f} deve ser ABAIXO da entrada {entry:.4f}"
            if tp1<= entry: return False, f"LONG: TP1 {tp1:.4f} deve ser ACIMA da entrada {entry:.4f}"
            if tp2>0 and tp2<=tp1: return False, f"LONG: TP2 {tp2:.4f} deve ser ACIMA do TP1 {tp1:.4f}"
        elif direction=="SHORT":
            if sl <= entry: return False, f"SHORT: SL {sl:.4f} deve ser ACIMA da entrada {entry:.4f}"
            if tp1>= entry: return False, f"SHORT: TP1 {tp1:.4f} deve ser ABAIXO da entrada {entry:.4f}"
            if tp2>0 and tp2>=tp1: return False, f"SHORT: TP2 {tp2:.4f} deve ser ABAIXO do TP1 {tp1:.4f}"
        return True, "OK"

    # ── Analise

    def analyze_market(self, symbol: str) -> dict:
        logger.info(f"Analisando {symbol}...")
        data     = self.analyzer.get_full_analysis(symbol)
        leverage = self.get_leverage_for(symbol)
        analysis = self._chat(
            f"{symbol}/USDC perp Hyperliquid. Alavancagem={leverage}x. NAO inclua leverage no JSON. Retorne APENAS o JSON.",
            data, symbol=symbol)
        signal   = self._extract_signal(analysis)
        if signal:
            signal["symbol"]   = symbol
            signal["leverage"] = leverage  # injeta alavancagem fixa do CONFIG
            logger.info(f"{symbol}: {signal.get('signal')} conf={signal.get('confidence')}% lev={leverage}x")
        return {"symbol":symbol,"analysis":analysis,"signal":signal}

    # ── Execucao

    def execute_trade(self, symbol: str, signal: dict) -> dict:
        if not signal or signal.get("signal")=="WAIT": return {"status":"skipped"}
        if self.daily_trades_count >= CONFIG["max_trades_day"]:
            return {"status":"blocked","reason":f"Limite {CONFIG['max_trades_day']} trades/dia"}

        direction  = signal["signal"]
        entry      = float(signal.get("entry_price",0))
        sl         = float(signal.get("stop_loss",0))
        tp1        = float(signal.get("take_profit_1",0))
        tp2        = float(signal.get("take_profit_2",0))
        confidence = signal.get("confidence",0)
        leverage   = self.get_leverage_for(symbol)

        ok, motivo = self._validate_prices(direction, entry, sl, tp1, tp2)
        if not ok:
            logger.warning(f"Precos invalidos [{symbol}]: {motivo}")
            self._notify(f"⚠️ <b>Sinal rejeitado — {symbol}</b>\n{motivo}")
            return {"status":"invalid_prices","reason":motivo}

        risk_d = abs(entry-sl); reward_d = abs(tp1-entry)
        rr = reward_d/risk_d if risk_d>0 else 0
        if rr < CONFIG["take_profit_ratio"]:
            return {"status":"bad_rr","reason":f"R:R {rr:.2f} < {CONFIG['take_profit_ratio']}"}

        acc    = self.hl.get_account_info()
        equity = acc.get("equity",0)
        if equity<=0: return {"status":"error","reason":"Equity zero"}

        val = self.risk.validate_trade(signal, self.active_positions, self.daily_pnl, total_equity=equity)
        if not val["approved"]:
            logger.warning(f"Risk bloqueou [{symbol}]: {val['reason']}")
            return {"status":"blocked","reason":val["reason"]}

        size = self.risk.calculate_position_size(equity, entry, sl)
        if size<=0: return {"status":"error","reason":"Size zero"}

        risk_pct = abs(entry-sl)/entry*100 if entry>0 else 0
        tp1_pct  = abs(tp1-entry)/entry*100 if entry>0 else 0
        tp2_pct  = abs(tp2-entry)/entry*100 if entry>0 else 0
        rr_ratio = round(reward_d/risk_d,2) if risk_d>0 else 0
        is_buy   = direction=="LONG"

        pos = {"direction":direction,"entry":entry,"stop_loss":sl,"tp1":tp1,"tp2":tp2,
               "size":size,"leverage":leverage,"equity_open":equity,
               "opened_at":datetime.now(timezone.utc).isoformat(),
               "peak_pnl":0.0,"tp1_hit":False,"status":"open",
               "entry_order_id":None,"sl_order_id":None,"tp1_order_id":None,"tp2_order_id":None}

        if CONFIG["dry_run"]:
            self.active_positions[symbol]=pos; self.stats["total"]+=1; self.daily_trades_count+=1
        else:
            try:
                self.hl.set_leverage(symbol, leverage, is_cross=False)
                cur_price  = self.hl.get_mark_price(symbol)
                diff_pct   = abs(entry-cur_price)/cur_price*100 if cur_price>0 else 99
                use_market = diff_pct <= CONFIG["market_order_threshold"]
                exec_price = cur_price*(1.001 if is_buy else 0.999) if use_market else entry
                otype      = "market" if use_market else "limit"
                logger.info(f"{symbol}: {otype} order diff={diff_pct:.3f}%")

                res = self.hl.place_order(symbol=symbol, is_buy=is_buy, size=size, price=exec_price, order_type=otype)
                if res.get("status") != "ok":
                    self._notify(f"⚠️ <b>ORDEM REJEITADA — {symbol}</b>\n<code>{str(res.get('raw',''))[:200]}</code>")
                    return {"status": "rejected", "reason": str(res)}

                entry_oid  = res.get("order_id")
                exec_ts    = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                pos["entry_order_id"] = entry_oid

                # Se resposta já veio com fill, usa avgPx direto
                if res.get("filled") and res.get("avg_px", 0) > 0:
                    real_entry = res["avg_px"]
                    pos["entry"] = real_entry
                    logger.info(f"Fill imediato: avgPx={real_entry}")
                else:
                    # Aguarda e verifica se foi preenchida
                    time.sleep(2)
                    open_oids = [str(o.get("oid", "")) for o in self.hl.get_open_orders()]
                    if str(entry_oid) in open_oids:
                        pos["status"] = "pending_fill"
                        self.active_positions[symbol] = pos
                        self.stats["total"] += 1
                        self.daily_trades_count += 1
                        self._notify(
                            f"⏳ <b>ORDEM ABERTA — {symbol}</b>\n────────────────────\n"
                            f"Tipo: <code>LIMIT {direction}</code>\n"
                            f"Preço: <code>${exec_price:,.4f}</code> | Size: <code>{size}</code>\n"
                            f"ID: <code>{entry_oid}</code>\n"
                            f"Status: <b>Aguardando preenchimento</b>\n"
                            f"🕐 {exec_ts}"
                        )
                        self._save_state()
                        return {"status": "pending_fill"}

                    # Preenchida — busca preço real via userFills
                    real_entry = exec_price
                    try:
                        last = self.hl.get_last_fill(symbol)
                        if last and last.get("price", 0) > 0:
                            real_entry = last["price"]
                            logger.info(f"Fill via userFills: px={real_entry} fee=${last.get('fee',0)}")
                    except Exception:
                        pass
                    pos["entry"] = real_entry

                pos = self._place_sl_tp(symbol, pos, size, is_buy, sl, tp1, tp2)
                pos["status"] = "open"
                self.active_positions[symbol] = pos
                self.stats["total"] += 1
                self.daily_trades_count += 1
                logger.info(f"{direction} {symbol} executado @ ${pos['entry']:,.4f} SL={pos['sl_order_id']} TP1={pos['tp1_order_id']}")
            except Exception as e:
                logger.error(f"Erro execucao {symbol}: {e}", exc_info=True)
                self._notify(f"🚨 <b>ERRO execucao {symbol}</b>\n<code>{str(e)[:200]}</code>")
                return {"status":"error","error":str(e)}

        self._save_state()
        em       = "🟢" if direction=="LONG" else "🔴"
        simple   = signal.get("reasoning_simples","")
        ganho    = signal.get("ganho_maximo","")
        perda    = signal.get("perda_maxima","")
        exec_now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        real_e   = pos.get("entry", entry)
        risk_pct = abs(real_e-sl)/real_e*100 if real_e>0 else 0
        tp1_pct  = abs(tp1-real_e)/real_e*100 if real_e>0 else 0
        tp2_pct  = abs(tp2-real_e)/real_e*100 if real_e>0 else 0

        msg = (f"{em} <b>ENTRADA EXECUTADA — {direction}</b>\n────────────────────\n"
               f"Par: <code>{symbol}/USDC</code> | Lev: <code>{leverage}x</code> | Conf: <code>{confidence}%</code>\n\n"
               f"💵 Executado: <code>${real_e:,.4f}</code>\n"
               f"📦 Size: <code>{size}</code> contratos\n"
               f"🛑 SL: <code>${sl:,.4f}</code> (-{risk_pct:.2f}%)\n"
               f"🎯 TP1: <code>${tp1:,.4f}</code> (+{tp1_pct:.2f}%)\n"
               f"🏆 TP2: <code>${tp2:,.4f}</code> (+{tp2_pct:.2f}%)\n\n"
               f"📐 R:R: <code>1:{rr_ratio}</code>\n")
        if not CONFIG["dry_run"]:
            sl_id  = pos.get("sl_order_id","?")
            tp1_id = pos.get("tp1_order_id","?")
            tp2_id = pos.get("tp2_order_id","?")
            msg += (f"\n🔗 IDs na Exchange:\n"
                    f"  SL: <code>{sl_id}</code>\n"
                    f"  TP1: <code>{tp1_id}</code>\n"
                    f"  TP2: <code>{tp2_id}</code>\n")
        if ganho:  msg += f"💰 {ganho}\n"
        if perda:  msg += f"⚠️ {perda}\n"
        if simple: msg += f"\n💬 {simple}\n"
        msg += f"\n🕐 {exec_now} | {'DRY RUN' if CONFIG['dry_run'] else '🔴 LIVE'}"
        self._notify(msg)
        return {"status":"dry_run" if CONFIG["dry_run"] else "executed"}

    def _place_sl_tp(self, symbol, pos, size, is_buy, sl, tp1, tp2):
        sl  = float(sl)
        tp1 = float(tp1)
        tp2 = float(tp2)
        try:
            r=self.hl.place_order(symbol=symbol,is_buy=not is_buy,size=size,price=sl,order_type="stop",reduce_only=True)
            pos["sl_order_id"]=r.get("order_id")
            logger.info(f"SL [{symbol}] colocado @ ${sl:.4f} oid={pos['sl_order_id']}")
        except Exception as e:
            logger.error(f"SL [{symbol}]: {e}")
            self._notify(f"🚨 ERRO SL — {symbol}: {e} — FECHE MANUALMENTE!")
        try:
            r=self.hl.place_order(symbol=symbol,is_buy=not is_buy,size=size*CONFIG["partial_tp_pct"],price=tp1,order_type="limit",reduce_only=True)
            pos["tp1_order_id"]=r.get("order_id")
            logger.info(f"TP1 [{symbol}] colocado @ ${tp1:.4f} oid={pos['tp1_order_id']}")
        except Exception as e: logger.error(f"TP1 [{symbol}]: {e}")
        try:
            r=self.hl.place_order(symbol=symbol,is_buy=not is_buy,size=size*(1-CONFIG["partial_tp_pct"]),price=tp2,order_type="limit",reduce_only=True)
            pos["tp2_order_id"]=r.get("order_id")
            logger.info(f"TP2 [{symbol}] colocado @ ${tp2:.4f} oid={pos['tp2_order_id']}")
        except Exception as e: logger.error(f"TP2 [{symbol}]: {e}")
        return pos

    # ── Monitor

    def monitor_positions(self):
        # Busca posições reais na exchange uma vez para todos os símbolos
        real_positions = {}
        if not CONFIG["dry_run"]:
            try:
                real_positions = self.hl.get_all_positions()
            except Exception as e:
                logger.error(f"monitor get_all_positions: {e}")

        for symbol, pos in list(self.active_positions.items()):
            current = self.hl.get_mark_price(symbol)
            if current == 0: continue

            # Verifica se posição ainda existe na exchange (apenas LIVE)
            if not CONFIG["dry_run"] and symbol not in real_positions:
                logger.warning(f"Posição {symbol} não encontrada na exchange — pode ter sido fechada externamente")
                self._notify(
                    f"⚠️ <b>Posição {symbol} não encontrada na exchange</b>\n"
                    f"Pode ter sido fechada por SL/TP ou manualmente.\n"
                    f"Removendo do estado interno.\n"
                    f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
                )
                self.trade_history.append({**pos, "symbol": symbol, "exit": current,
                                           "exit_reason": "Fechada externamente", "pnl_usd": 0})
                del self.active_positions[symbol]
                self._save_state()
                continue

            if pos.get("status") == "pending_fill" and not CONFIG["dry_run"]:
                open_oids = [str(o.get("oid","")) for o in self.hl.get_open_orders()]
                if str(pos.get("entry_order_id","")) not in open_oids:
                    try:
                        is_buy = pos["direction"] == "LONG"
                        # Pega preço real de entrada da exchange
                        if symbol in real_positions:
                            pos["entry"] = real_positions[symbol]["entry"]
                        pos = self._place_sl_tp(symbol, pos, pos["size"], is_buy, pos["stop_loss"], pos["tp1"], pos["tp2"])
                        pos["status"] = "open"
                        self.active_positions[symbol] = pos
                        self._notify(
                            f"✅ <b>FILL CONFIRMADO — {symbol}</b>\n────────────────────\n"
                            f"Entrada real: <code>${pos['entry']:,.4f}</code>\n"
                            f"SL: <code>{pos.get('sl_order_id','?')}</code>\n"
                            f"TP1: <code>{pos.get('tp1_order_id','?')}</code>\n"
                            f"TP2: <code>{pos.get('tp2_order_id','?')}</code>\n"
                            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
                        )
                        self._save_state()
                    except Exception as e:
                        logger.error(f"pos fill {symbol}: {e}")
                        self._notify(f"🚨 FILL ok mas SL/TP falharam — {symbol}: {e}")
                continue

            is_long = pos["direction"] == "LONG"
            entry   = float(pos.get("entry", 0) or current)
            if entry == 0: entry = current
            pnl_pct = ((current-entry)/entry*100) if is_long else ((entry-current)/entry*100)
            pos["peak_pnl"] = max(pos["peak_pnl"], pnl_pct)

            # P&L real da exchange se disponível
            real_pnl_usd = real_positions.get(symbol, {}).get("unrealized_pnl", None)

            lev     = pos.get("leverage", 1)
            liq_pct = 80/lev
            loss    = -pnl_pct if pnl_pct < 0 else 0
            if loss > liq_pct*0.7:
                self._notify(
                    f"🚨 <b>ALERTA LIQUIDAÇÃO — {symbol}</b>\n"
                    f"Perda: <code>{loss:.1f}%</code> (liquidação em ~{liq_pct:.1f}%)\n"
                    f"P&L: <code>${real_pnl_usd:+.2f}</code>\n"
                    f"Use /fechar {symbol} para fechar agora!\n"
                    f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
                )

            sl  = float(pos.get("stop_loss", 0) or 0)
            tp1 = float(pos.get("tp1", 0) or 0)
            tp2 = float(pos.get("tp2", 0) or 0)

            hit_sl  = sl  > 0 and ((is_long and current<=sl)  or (not is_long and current>=sl))
            hit_tp1 = tp1 > 0 and (not pos["tp1_hit"]) and ((is_long and current>=tp1) or (not is_long and current<=tp1))
            hit_tp2 = tp2 > 0 and pos["tp1_hit"] and ((is_long and current>=tp2) or (not is_long and current<=tp2))

            if hit_sl:
                self._close_position(symbol, pos, current, "Stop Loss atingido")
            elif hit_tp2:
                self._close_position(symbol, pos, current, "TP2 atingido")
            elif hit_tp1:
                partial = pos["size"]*CONFIG["partial_tp_pct"]
                if not CONFIG["dry_run"]:
                    self.hl.place_order(symbol=symbol, is_buy=not is_long, size=partial, price=current, order_type="market", reduce_only=True)
                pos["size"] -= partial
                pos["stop_loss"] = entry
                pos["tp1_hit"]   = True
                if not CONFIG["dry_run"] and pos.get("sl_order_id"):
                    try:
                        self.hl.cancel_order(symbol, pos["sl_order_id"])
                        r = self.hl.place_order(symbol=symbol, is_buy=not is_long, size=pos["size"], price=entry, order_type="stop", reduce_only=True)
                        pos["sl_order_id"] = r.get("order_id")
                    except Exception as e:
                        logger.error(f"Breakeven SL {symbol}: {e}")
                lev_pnl = pnl_pct*lev
                self._notify(
                    f"🎯 <b>TP1 ATINGIDO — {symbol}</b>\n────────────────────\n"
                    f"Resultado parcial: <code>+{lev_pnl:.2f}%</code>\n"
                    f"SL movido para entrada: <code>${entry:,.4f}</code>\n"
                    f"Próximo alvo TP2: <code>${tp2:,.4f}</code>\n"
                    f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
                )
                self._save_state()
            elif pos["tp1_hit"] and CONFIG.get("trailing_stop"):
                atr = self._get_atr(symbol)
                if atr > 0:
                    new_sl   = self.risk.calculate_trailing_stop(current, pos["direction"], atr)
                    improved = (is_long and new_sl>pos["stop_loss"]) or (not is_long and new_sl<pos["stop_loss"])
                    if improved:
                        old_sl = pos["stop_loss"]
                        pos["stop_loss"] = new_sl
                        logger.info(f"Trailing {symbol}: ${old_sl:.4f}->${new_sl:.4f}")
                        if not CONFIG["dry_run"] and pos.get("sl_order_id"):
                            try:
                                self.hl.cancel_order(symbol, pos["sl_order_id"])
                                r = self.hl.place_order(symbol=symbol, is_buy=not is_long, size=pos["size"], price=new_sl, order_type="stop", reduce_only=True)
                                pos["sl_order_id"] = r.get("order_id")
                                self._save_state()
                            except Exception as e:
                                logger.error(f"Trailing SL {symbol}: {e}")

    def _get_atr(self, symbol, period=14):
        try:
            candles=self.hl.get_candles(symbol,"1h",period+2)
            if not candles or len(candles)<period: return 0
            highs,lows,closes=[],[],[]
            for c in candles:
                if isinstance(c,dict): highs.append(float(c.get("h",0))); lows.append(float(c.get("l",0))); closes.append(float(c.get("c",0)))
                elif isinstance(c,(list,tuple)) and len(c)>=5: highs.append(float(c[1])); lows.append(float(c[2])); closes.append(float(c[3]))
            trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(highs))]
            return sum(trs[-period:])/period if trs else 0
        except: return 0

    def _close_position(self, symbol, pos, exit_price, reason):
        is_long = pos["direction"] == "LONG"
        entry   = float(pos.get("entry", 0) or exit_price)
        pnl_pct = ((exit_price-entry)/entry*100) if is_long else ((entry-exit_price)/entry*100)
        pnl_lev = pnl_pct * pos["leverage"]
        pnl_usd = (pnl_pct/100) * pos["equity_open"]
        try:
            opened  = datetime.fromisoformat(pos["opened_at"].replace("Z","").replace("+00:00",""))
            dur_min = round((datetime.now(timezone.utc)-opened.replace(tzinfo=timezone.utc)).total_seconds()/60)
        except:
            dur_min = 0

        if not CONFIG["dry_run"]:
            for k in ("sl_order_id","tp1_order_id","tp2_order_id"):
                oid = pos.get(k)
                if oid:
                    try: self.hl.cancel_order(symbol, oid)
                    except: pass
            try: self.hl.close_position(symbol, pos["size"], is_long)
            except Exception as e: logger.error(f"close {symbol}: {e}")

            # Busca dados reais do fechamento via userFills
            time.sleep(1)
            try:
                last = self.hl.get_last_fill(symbol)
                if last and last.get("price", 0) > 0:
                    exit_price = last["price"]
                    real_fee   = last.get("fee", 0)
                    real_pnl   = last.get("closed_pnl", pnl_usd)
                    # Recalcula com preço real
                    pnl_pct = ((exit_price-entry)/entry*100) if is_long else ((entry-exit_price)/entry*100)
                    pnl_lev = pnl_pct * pos["leverage"]
                    pnl_usd = real_pnl if real_pnl != 0 else pnl_usd
                    logger.info(f"Close real: px={exit_price} fee=${real_fee} pnl=${pnl_usd}")
            except Exception as e:
                logger.error(f"fills close {symbol}: {e}")
                real_fee = 0
        else:
            real_fee = 0

        # Busca funding acumulado da posição
        funding_usd = 0
        if not CONFIG["dry_run"]:
            try:
                fs = self.hl.get_funding_summary(symbol)
                funding_usd = fs.get("total_funding_usd", 0)
            except Exception: pass

        em = "✅" if pnl_usd >= 0 else "❌"
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        msg = (f"{em} <b>SAÍDA — {'LUCRO' if pnl_usd>=0 else 'PERDA'}</b>\n────────────────────\n"
               f"<b>{symbol}</b> {pos['direction']} {pos['leverage']}x\n"
               f"Motivo: <code>{reason}</code> | Duração: <code>{dur_min}min</code>\n\n"
               f"📥 Entrada: <code>${entry:,.4f}</code>\n"
               f"📤 Saída:   <code>${exit_price:,.4f}</code>\n\n"
               f"<b>Resultado: <code>{pnl_lev:+.2f}%</code> (<code>${pnl_usd:+.2f}</code>)</b>\n")
        if real_fee:
            msg += f"💸 Fee pago: <code>${real_fee:.4f}</code>\n"
        if funding_usd:
            em_f = "📈" if funding_usd >= 0 else "📉"
            msg += f"{em_f} Funding: <code>${funding_usd:+.4f}</code>\n"
        msg += f"\n🕐 {now}"
        self._notify(msg)

        self.session_pnl += pnl_usd
        self.daily_pnl   += pnl_usd
        if pnl_usd >= 0:
            self.stats["wins"]  += 1
            self.stats["best"]   = max(self.stats["best"], pnl_usd)
        else:
            self.stats["losses"] += 1
            self.stats["worst"]  = min(self.stats["worst"], pnl_usd)

        self.trade_history.append({
            **pos, "symbol": symbol, "exit": exit_price,
            "exit_reason": reason, "pnl_usd": pnl_usd,
            "fee": real_fee, "duration_min": dur_min,
            "closed_at": datetime.now(timezone.utc).isoformat()
        })
        del self.active_positions[symbol]
        self._save_state()

    def notify_pnl_update(self):
        if not self.active_positions: return
        total = self.stats["total"]
        wr    = round(self.stats["wins"]/total*100,1) if total>0 else 0
        unr   = 0.0
        now   = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        # Busca P&L real da exchange
        real_pnls = {}
        if not CONFIG["dry_run"]:
            try:
                acc = self.hl.get_account_info()
                for p in acc.get("positions",[]):
                    pd  = p.get("position",{})
                    sym = pd.get("coin","")
                    real_pnls[sym] = float(pd.get("unrealizedPnl",0) or 0)
            except Exception: pass

        lines = [f"📊 <b>UPDATE — {now}</b>", "────────────────────"]
        for sym, pos in self.active_positions.items():
            cur   = self.hl.get_mark_price(sym)
            entry = float(pos.get("entry",0) or cur)
            il    = pos["direction"]=="LONG"
            pp    = ((cur-entry)/entry*100) if il else ((entry-cur)/entry*100)
            pl    = pp*pos["leverage"]
            pu    = real_pnls.get(sym, (pp/100)*pos["equity_open"])
            unr  += pu
            em    = "🟢" if pu>=0 else "🔴"
            sl    = float(pos.get("stop_loss",0) or 0)
            tp1   = float(pos.get("tp1",0) or 0)
            opened = pos.get("opened_at","")[:16].replace("T"," ")
            lines.append(
                f"{em} <b>{sym}</b> {'LONG' if il else 'SHORT'} {pos['leverage']}x\n"
                f"  Entrada: <code>${entry:,.4f}</code> | Atual: <code>${cur:,.4f}</code>\n"
                f"  P&L: <code>{pl:+.2f}%</code> (<code>${pu:+.2f}</code>)\n"
                f"  SL: <code>${sl:,.4f}</code> | TP1: <code>${tp1:,.4f}</code>\n"
                f"  Aberto: <code>{opened} UTC</code>"
            )
        lines += [
            "────────────────────",
            f"💰 Não realizado: <code>${unr:+.2f}</code>",
            f"💵 Realizado hoje: <code>${self.daily_pnl:+.2f}</code>",
            f"📈 Win Rate: <code>{wr}%</code> ({self.stats['wins']}W/{self.stats['losses']}L)",
            f"🕐 {now}"
        ]
        self._notify("\n".join(lines))

    def generate_daily_report(self) -> str:
        total=self.stats["total"]; wr=round(self.stats["wins"]/total*100,1) if total>0 else 0
        return self._chat("Gere relatorio executivo da sessao em portugues.",
            {"session_pnl":self.session_pnl,"total":total,"wr":f"{wr}%",
             "wins":self.stats["wins"],"losses":self.stats["losses"]},symbol="_report")

    def _notify(self, msg: str):
        if self.telegram_bot and CONFIG["telegram_chat_id"]:
            try:
                loop=asyncio.get_event_loop()
                if loop and loop.is_running(): asyncio.ensure_future(self.telegram_bot.notify(CONFIG["telegram_chat_id"],msg))
                else: loop.run_until_complete(self.telegram_bot.notify(CONFIG["telegram_chat_id"],msg))
            except Exception as e: logger.error(f"Notify: {e}")

    async def run_autonomous(self):
        logger.info("Loop autonomo iniciado")
        self._notify(
            "🚀 <b>HyperTrader ATIVO</b>\n────────────────────\n"
            f"🌐 {'TESTNET' if CONFIG['use_testnet'] else 'MAINNET'}\n"
            f"⚙️ {'DRY RUN — Simulado' if CONFIG['dry_run'] else '🔴 LIVE — Dinheiro real'}\n"
            f"🎯 Pares: <code>{', '.join(CONFIG['symbols'])}</code>\n"
            f"💰 Risco/trade: <code>{CONFIG['risk_per_trade']*100:.0f}%</code> | Lev: <code>BTC/ETH=5x SOL/AVAX=3x</code>\n"
            f"⏱ Intervalo: <code>{CONFIG['analysis_interval']}s</code>")
        pnl_tick=0
        while True:
            try:
                if self.paused: await asyncio.sleep(30); continue
                today=datetime.now(timezone.utc).date()
                if today!=self._current_day:
                    old_p=self.daily_pnl; old_t=self.daily_trades_count
                    self.daily_pnl=0.0; self.daily_trades_count=0; self._current_day=today
                    self._notify(f"🌅 <b>Novo dia</b> | P&amp;L ontem: <code>${old_p:+.2f}</code> | Trades: <code>{old_t}</code>")

                acc_val=self.hl.get_account_info().get("equity",0)
                self.risk.update_peak(acc_val)
                metrics=self.risk.get_risk_metrics(acc_val,self.active_positions)
                dd_pct=metrics["drawdown_pct"]; dd_st=metrics["drawdown_status"]

                daily_lim=metrics["trading_capital"]*CONFIG["daily_loss_limit"]
                if self.daily_pnl<0 and daily_lim>0 and abs(self.daily_pnl)>=daily_lim:
                    self._notify(f"⛔ <b>STOP DIARIO</b> | Perda: <code>${self.daily_pnl:.2f}</code> | Lim: <code>${daily_lim:.2f}</code>\nPausado 2h.")
                    await asyncio.sleep(7200); self.daily_pnl=0.0; continue

                self.monitor_positions()
                pnl_tick+=10
                if pnl_tick>=CONFIG["pnl_update_interval"]: self.notify_pnl_update(); pnl_tick=0

                resultados=[]
                for sym in CONFIG["symbols"]:
                    if self.paused: break
                    if sym in self.active_positions:
                        pos=self.active_positions[sym]; cur=self.hl.get_mark_price(sym)
                        il=pos["direction"]=="LONG"
                        pp=((cur-pos["entry"])/pos["entry"]*100) if il else ((pos["entry"]-cur)/pos["entry"]*100)
                        pl=pp*pos["leverage"]; pu=(pp/100)*pos["equity_open"]
                        em="🟢" if pu>=0 else "🔴"; st="⏳" if pos.get("status")=="pending_fill" else ""
                        resultados.append(f"{em}{st} <b>{sym}</b> em posicao | <code>{pl:+.2f}%</code>"); continue
                    if len(self.active_positions)>=CONFIG["max_positions"]:
                        resultados.append(f"⏸ <b>{sym}</b> — aguardando vaga"); continue
                    r=self.analyze_market(sym); s=r.get("signal")
                    sig=s.get("signal","WAIT") if s else "WAIT"; conf=s.get("confidence",0) if s else 0
                    momento=s.get("momento","") if s else ""
                    if sig=="LONG":    em,dp="🟢","COMPRAR"
                    elif sig=="SHORT": em,dp="🔴","VENDER"
                    else:              em,dp="⚪","ESPERAR"
                    linha=f"{em} <b>{sym}</b> — {dp} <code>{conf}%</code>"
                    if momento: linha+=f"\n    <i>{str(momento)[:120]}</i>"
                    resultados.append(linha)
                    if sig in ("LONG","SHORT") and conf>=CONFIG["min_confidence"]: self.execute_trade(sym,s)
                    await asyncio.sleep(8)

                total=self.stats["total"]; wr=round(self.stats["wins"]/total*100,1) if total>0 else 0
                prx_ts=datetime.now(timezone.utc).timestamp()+CONFIG["analysis_interval"]
                prx_str=datetime.fromtimestamp(prx_ts,tz=timezone.utc).strftime("%H:%M UTC")
                self._notify(
                    "🔄 <b>Ciclo Completo</b>\n────────────────────\n"+"\n".join(resultados)+
                    "\n\n────────────────────\n"
                    f"💼 Capital: <code>${acc_val:,.2f}</code>\n"
                    f"📉 Drawdown: <code>{dd_pct:.1f}%</code> {dd_st}\n"
                    f"📊 Posicoes: <code>{len(self.active_positions)}/{CONFIG['max_positions']}</code>\n"
                    f"🔢 Trades hoje: <code>{self.daily_trades_count}/{CONFIG['max_trades_day']}</code>\n"
                    f"💵 P&amp;L hoje: <code>${self.daily_pnl:+.2f}</code> | sessao: <code>${self.session_pnl:+.2f}</code>\n"
                    f"🏆 Win Rate: <code>{wr}%</code> ({self.stats['wins']}W/{self.stats['losses']}L)\n"
                    f"⏱ Proxima: <code>{prx_str}</code>")
                logger.info(f"Ciclo | pos={len(self.active_positions)} | pnl=${self.session_pnl:+.2f} | prx={prx_str}")
                await asyncio.sleep(CONFIG["analysis_interval"])
            except Exception as e:
                logger.error(f"Erro loop: {e}", exc_info=True)
                self._notify(f"🚨 <b>Erro loop</b>\n<code>{str(e)[:200]}</code>")
                await asyncio.sleep(30)


if __name__=="__main__":
    import sys
    agent=HyperTraderAgent(); mode=sys.argv[1] if len(sys.argv)>1 else "interactive"
    if mode=="auto":
        async def main_async():
            if CONFIG["telegram_token"]:
                from telegram_bot import TelegramBot
                tg=TelegramBot(agent,CONFIG["telegram_token"]); agent.telegram_bot=tg
                await tg.app.initialize(); await tg.app.start()
                await tg.app.updater.start_polling(drop_pending_updates=True)
                try: await agent.run_autonomous()
                finally:
                    await tg.app.updater.stop(); await tg.app.stop(); await tg.app.shutdown()
            else: await agent.run_autonomous()
        asyncio.run(main_async())
    else:
        while True:
            try:
                ui=input("Voce: ").strip()
                if not ui or ui.lower() in ("sair","exit"): break
                elif ui.lower().startswith("analisar "):
                    sym=ui.split()[-1].upper(); r=agent.analyze_market(sym)
                    print(f"\n{r['analysis']}\n")
                    if r["signal"] and r["signal"].get("signal")!="WAIT": agent.execute_trade(sym,r["signal"])
                elif ui.lower()=="posicoes": print(json.dumps(agent.active_positions,indent=2,default=str))
                else: print(f"\nAgente: {agent._chat(ui,symbol='_chat')}\n")
            except KeyboardInterrupt: break