"""
config.py — Configurações centralizadas do HyperTrader.

Princípio Clean Code: "Separe configuração de lógica."
Todas as constantes e variáveis de ambiente ficam aqui.
"""
import os
from dotenv import load_dotenv

load_dotenv()


# ── Configurações de trading
SYMBOLS          = ["BTC", "ETH", "SOL", "AVAX"]
MAX_POSITIONS    = 4
RISK_PER_TRADE   = 0.02          # 2% do capital por trade
MAX_TRADES_DAY   = 6
DAILY_LOSS_LIMIT = 0.06          # 6% de perda diária = stop total
DRAWDOWN_HALF    = 10.0          # % drawdown para reduzir risco pela metade
DRAWDOWN_STOP    = 20.0          # % drawdown para parar completamente
MIN_CONFIDENCE   = 60            # % mínimo de confiança do sinal
TAKE_PROFIT_RATIO = 1.5          # R:R mínimo para entrar
PARTIAL_TP_PCT   = 0.50          # % do size fechado no TP1
TRAILING_STOP    = True
MARKET_ORDER_THRESHOLD = 0.3     # % de distância para usar market order

# ── Alavancagem por símbolo
LEVERAGE_MAP = {
    "BTC": 5, "ETH": 5,
    "SOL": 3, "AVAX": 3,
    "PEPE": 2,
}
MAX_LEVERAGE      = 10
LEVERAGE_FALLBACK = 3

# ── Intervalos (segundos)
ANALYSIS_INTERVAL  = 1800   # a cada 30 min
PNL_UPDATE_INTERVAL = 120   # update de P&L a cada 2 min
MONITOR_FREQ       = 30     # checa SL/TP a cada 30s dentro do intervalo

# ── Exchange
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"
DRY_RUN     = os.getenv("DRY_RUN", "true").lower() == "true"

# ── Credenciais
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
HL_WALLET_ADDRESS  = os.getenv("HL_WALLET_ADDRESS", "")
HL_PRIVATE_KEY     = os.getenv("HL_PRIVATE_KEY", "")
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
TELEGRAM_ALLOWED   = os.getenv("TELEGRAM_ALLOWED_IDS", "0")

# ── Modelos Claude
MODEL_TRADING = "claude-haiku-4-5-20251001"   # análise de mercado (rápido + barato)
MODEL_REPORT  = "claude-sonnet-4-6"            # relatórios e chat (mais inteligente)

# ── Arquivo de estado
STATE_FILE = "state.json"
