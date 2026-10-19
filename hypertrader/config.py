"""
config.py — Configurações centralizadas do HyperTrader.

Princípio Clean Code: "Separe configuração de lógica."
Todas as constantes e variáveis de ambiente ficam aqui.

V2 — Features ligadas, disciplina recalibrada.
"""
import os
from dotenv import load_dotenv

from pathlib import Path

load_dotenv()


# ── Estilo de trading (V1)
# V1 "4h/1h": 4h define tendência (filtro), 1h define gatilho de entrada.
TRADING_STYLE = os.getenv("TRADING_STYLE", "4h_1h").lower()

# ── Configurações de trading
SYMBOLS          = ["BTC", "ETH", "SOL", "AVAX"]

# V1 recomenda começar conservador e evoluir por config.
# 0.015 = 1.5% por trade (ainda agressivo). Ajuste via env RISK_PER_TRADE.
RISK_PER_TRADE   = float(os.getenv("RISK_PER_TRADE", "0.015"))

# Teto operacional (não meta): o bot só entra se o sinal passar no RiskManager.
MAX_TRADES_DAY   = int(os.getenv("MAX_TRADES_DAY", "2"))
MAX_POSITIONS    = int(os.getenv("MAX_POSITIONS", "2"))

# Stop diário: pausa novas entradas (monitor continua).
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "0.05"))
DRAWDOWN_HALF    = 10.0          # % drawdown para reduzir risco pela metade
DRAWDOWN_STOP    = 20.0          # % drawdown para parar completamente
MIN_CONFIDENCE   = 60            # % mínimo de confiança do sinal
TAKE_PROFIT_RATIO = 1.5          # R:R mínimo para entrar
PARTIAL_TP_PCT   = 0.50          # % do size fechado no TP1
TRAILING_STOP    = os.getenv("TRAILING_STOP", "true").lower() == "true"
MARKET_ORDER_THRESHOLD = 0.3     # % de distância para usar market order

# ── Features — LIGADAS para dar contexto ao Claude
# Com contexto macro/orderbook/estrutura/padrões, o Claude decide melhor
# e deixa de ser conservador por falta de informação.
ENABLE_MACRO      = os.getenv("ENABLE_MACRO", "true").lower() == "true"
ENABLE_ORDERBOOK  = os.getenv("ENABLE_ORDERBOOK", "true").lower() == "true"
ENABLE_STRUCTURE  = os.getenv("ENABLE_STRUCTURE", "true").lower() == "true"
ENABLE_PATTERNS   = os.getenv("ENABLE_PATTERNS", "true").lower() == "true"

# ── Alavancagem por símbolo
# "auto": usa alavancagem baixa e consistente (profissional/robusto).
# "max": tenta usar o máximo da moeda (NÃO recomendado; risco alto). Ainda respeita caps.
LEVERAGE_MODE     = os.getenv("LEVERAGE_MODE", "auto").lower()

# Cap padrão do V1 (segurança). Você pode aumentar via env LEVERAGE_CAP.
LEVERAGE_CAP      = int(os.getenv("LEVERAGE_CAP", "5"))

# Preferência por símbolo (só usada em modo "auto").
LEVERAGE_MAP = {
    "BTC": 5, "ETH": 5,
    "SOL": 3, "AVAX": 3,
}

# Hard cap final (não excede isso mesmo em "max", a menos que você altere por env).
MAX_LEVERAGE      = int(os.getenv("MAX_LEVERAGE", "20"))
LEVERAGE_FALLBACK = int(os.getenv("LEVERAGE_FALLBACK", "3"))

# ── Intervalos (segundos)
ANALYSIS_INTERVAL  = 1800   # a cada 30 min
# PNL_UPDATE_INTERVAL removido — update só via comando /pnl no Telegram
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


MODEL_TRADING = os.getenv("MODEL_TRADING", "claude-haiku-4-5-20251001")
MODEL_REPORT  = os.getenv("MODEL_REPORT",  "claude-haiku-4-5-20251001")

# ── Arquivo de estado
# Diretório de dados persistentes (state, journal, etc.)
DATA_DIR = os.getenv("DATA_DIR", "data")
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

STATE_FILE = str(Path(DATA_DIR) / "state.json")

# Journal append-only (histórico "para sempre")
JOURNAL_FILE = str(Path(DATA_DIR) / "journal.ndjson")

# Quantos trades fechados manter dentro do state.json (o journal mantém tudo)
TRADE_HISTORY_KEEP = int(os.getenv("TRADE_HISTORY_KEEP", "5000"))

# ── Auto-controle (profissionalismo)
# DESLIGADO: parar após o 1º win é conservador demais para swing trade.
# Se quiser religar no futuro, mude via env STOP_AFTER_FIRST_WIN=true.
STOP_AFTER_FIRST_WIN = os.getenv("STOP_AFTER_FIRST_WIN", "false").lower() == "true"

# Se perder muitas vezes seguidas, reduz risco e/ou pausa para "revisar".
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
LOSS_PAUSE_HOURS = float(os.getenv("LOSS_PAUSE_HOURS", "2"))
LOSS_RISK_MULTIPLIER = float(os.getenv("LOSS_RISK_MULTIPLIER", "0.5"))
LOSS_CONFIDENCE_ADD = int(os.getenv("LOSS_CONFIDENCE_ADD", "5"))

# Meta de patrimônio (USDC). Se 0, desativado.
TARGET_EQUITY = float(os.getenv("TARGET_EQUITY", "0") or 0)
ON_TARGET_ACTION = (os.getenv("ON_TARGET_ACTION", "pause") or "pause").lower()

# ── Fuso horário para exibição nas mensagens Telegram
# Use o nome IANA da sua timezone. Exemplos:
#   "America/Sao_Paulo"   → Brasília (UTC-3 / UTC-2 no verão)
#   "America/New_York"    → Nova York
#   "Europe/London"       → Londres
#   "UTC"                 → sem conversão (padrão antigo)
DISPLAY_TIMEZONE = os.getenv("DISPLAY_TIMEZONE", "America/Sao_Paulo")