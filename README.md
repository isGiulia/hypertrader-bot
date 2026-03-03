# 🤖 HyperTrader AI Bot

Bot autônomo de trading de perpetuals na **Hyperliquid** usando **Claude AI** como motor de análise técnica e macro.

Analisa BTC, ETH, SOL, AVAX e PEPE a cada 30 minutos, toma decisões de LONG/SHORT baseadas em indicadores técnicos + Fear & Greed Index + Dominância do BTC, e envia todas as notificações via Telegram.

---

## 📋 Pré-requisitos

- VPS com Ubuntu 22/24 (mínimo 1GB RAM)
- Python 3.10 ou superior
- Conta na [Hyperliquid](https://app.hyperliquid.xyz) com saldo
- Chave de API da [Anthropic](https://console.anthropic.com)
- Bot no Telegram (criado pelo [@BotFather](https://t.me/BotFather))

---

## 🚀 Instalação do zero

### 1. Clonar o repositório

```bash
git clone https://github.com/isGiulia/hypertrader-bot
cd hypertrader-bot
```

### 2. Criar o ambiente virtual Python

```bash
python3 -m venv .venv
```

### 3. Ativar o ambiente virtual

```bash
source .venv/bin/activate
```

> ⚠️ **Importante:** sempre que abrir um terminal novo na VPS, rode esse comando antes de qualquer outro. O terminal vai mostrar `(.venv)` no início da linha quando estiver ativo.

### 4. Instalar as dependências

```bash
pip install -r requirements.txt
pip install hyperliquid-python-sdk
```

### 5. Criar o arquivo de configuração `.env`

O arquivo `.env` **não existe no repositório** — você cria ele manualmente com suas credenciais:

```bash
nano .env
```

Cole o conteúdo abaixo substituindo pelos seus dados reais:

```env
# ── Anthropic (Claude AI) ──────────────────────────────────
# Obtenha em: https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY=sk-ant-XXXXXXXXXXXXXXXXXXXXXXXX

# ── Hyperliquid ────────────────────────────────────────────
# Endereço público da sua carteira (ex: MetaMask, Rabby)
HL_WALLET_ADDRESS=0xSEU_ENDERECO_AQUI

# Chave privada da sua carteira — NUNCA compartilhe isso!
# Para exportar da Rabby: Menu → Gerenciar Endereço → Backup da Chave Privada
HL_PRIVATE_KEY=0xSUA_CHAVE_PRIVADA_AQUI

# ── Telegram ───────────────────────────────────────────────
# Token do bot criado no @BotFather
TELEGRAM_BOT_TOKEN=0000000000:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

# Seu Chat ID (obtenha enviando /start para @userinfobot)
TELEGRAM_CHAT_ID=000000000

# Seu Chat ID novamente (define quem pode controlar o bot)
TELEGRAM_ALLOWED_IDS=000000000

# ── Modo de operação ───────────────────────────────────────
# True = usa a rede de testes (dinheiro simulado) — RECOMENDADO para começar
# False = usa a rede real (dinheiro real)
USE_TESTNET=True

# True = simula trades sem enviar ordens para a exchange
# False = executa ordens reais na exchange
DRY_RUN=True
```

Salvar: `Ctrl+O` → Enter → `Ctrl+X`

---

## ▶️ Iniciando o bot

### Rodar em segundo plano (recomendado para VPS)

```bash
source .venv/bin/activate
nohup python main.py auto > bot.log 2>&1 &
```

### Ver os logs em tempo real

```bash
tail -f bot.log
```

### Parar o bot

```bash
pkill -9 -f python
```

### Reiniciar o bot (atualizar código)

```bash
pkill -9 -f python
git pull origin master
source .venv/bin/activate
nohup python main.py auto > bot.log 2>&1 &
sleep 3 && tail -f bot.log
```

---

## 🧪 Testando antes de usar dinheiro real

### Passo 1 — Testar o testnet

Primeiro rode no testnet com dinheiro simulado. No `.env`:

```env
USE_TESTNET=True
DRY_RUN=False
```

Para obter $1.000 USDC simulado no testnet:
1. Deposite qualquer valor na Hyperliquid mainnet (mínimo $1)
2. Acesse: https://app.hyperliquid-testnet.xyz/drip
3. Conecte a mesma carteira e clique em "Claim 1000 Mock USDC"

### Passo 2 — Confirmar que ordens chegam na exchange

Acesse https://app.hyperliquid-testnet.xyz → Portfolio → Open Orders

Se aparecerem ordens lá, o bot está funcionando corretamente.

### Passo 3 — Observar por pelo menos 1 semana

Veja se os trades são lucrativos antes de ir para dinheiro real.

### Passo 4 — Ir para o mainnet

Apenas quando estiver satisfeito com os resultados do testnet:

```env
USE_TESTNET=False
DRY_RUN=False
```

> ⚠️ **ATENÇÃO:** Nunca vá para mainnet sem testar no testnet primeiro. Um bug com alavancagem pode liquidar a conta inteira.

---

## 📱 Comandos do Telegram

Após iniciar o bot, envie esses comandos para ele no Telegram:

| Comando | Função |
|---------|--------|
| `/start` | Inicia e mostra painel principal |
| `/status` | Situação atual do bot |
| `/posicoes` | Posições abertas com P&L ao vivo |
| `/pnl` | Resumo de lucros e perdas |
| `/analisar BTC` | Análise manual de um par |
| `/pausar` | Pausa o bot (não abre novos trades) |
| `/retomar` | Retoma o bot pausado |
| `/fechar BTC` | Fecha posição manualmente |
| `/relatorio` | Relatório completo da sessão |
| `/historico` | Últimos trades realizados |
| `/config` | Configurações atuais |
| `/ajuda` | Lista todos os comandos |

---

## ⚙️ Configurações principais

Todas no `main.py` dentro do dicionário `CONFIG`:

| Configuração | Padrão | Descrição |
|---|---|---|
| `symbols` | BTC, ETH, SOL, AVAX, PEPE | Pares analisados |
| `max_positions` | 5 | Máximo de posições abertas |
| `max_leverage` | 10x | Alavancagem máxima permitida |
| `max_trades_day` | 3 | Limite de trades por dia |
| `risk_per_trade` | 2.5% | Risco máximo por operação |
| `min_confidence` | 75% | Confiança mínima para entrar |
| `daily_loss_limit` | 6% | Stop diário automático |
| `analysis_interval` | 1800s | Intervalo entre análises (30min) |

---

## 🔐 Segurança

**Chave privada:**
- A chave privada no `.env` dá acesso total à sua carteira
- Nunca compartilhe o arquivo `.env` com ninguém
- Nunca suba o `.env` para o GitHub (ele está no `.gitignore`)
- Considere criar uma carteira separada só para o bot com capital limitado

**Servidor:**
- Use senha forte ou chave SSH no seu servidor
- Mantenha o Ubuntu atualizado: `apt update && apt upgrade`

---

## 📁 Estrutura dos arquivos

```
hypertrader-bot/
├── main.py                 # Motor principal — loop, execução, Telegram
├── market_analyzer.py      # Análise técnica: RSI, MACD, Bollinger, ATR, etc.
├── hyperliquid_client.py   # Comunicação com a API da Hyperliquid (EIP-712)
├── risk_manager.py         # Gestão de risco: tamanho, R:R, correlação
├── telegram_bot.py         # Interface Telegram: comandos e notificações
├── requirements.txt        # Dependências Python
├── .env                    # ⚠️ Suas credenciais — NUNCA suba para o GitHub
├── state.json              # Estado salvo automaticamente (posições, P&L)
└── logs/
    └── hypertrader.log     # Log completo de tudo que o bot faz
```

---

## 🛠️ Solução de problemas

**Bot não inicia:**
```bash
source .venv/bin/activate
python main.py auto
# Ver o erro sem o nohup para debugar
```

**Telegram não recebe mensagens:**
- Confirme que `TELEGRAM_BOT_TOKEN` está correto
- Confirme que `TELEGRAM_CHAT_ID` está correto (use @userinfobot para descobrir)
- Envie `/start` para o bot no Telegram primeiro

**Erro "SDK not initialized":**
```bash
pip install hyperliquid-python-sdk
```

**Erro "does not exist on mainnet":**
- Seu endereço precisa ter feito pelo menos um depósito na Hyperliquid mainnet
- Deposite qualquer valor mínimo em https://app.hyperliquid.xyz

**Ver logs de erro:**
```bash
grep "ERROR" bot.log | tail -20
```

---

## 💰 Custo estimado de operação

| Item | Custo |
|---|---|
| Claude AI (Haiku) | ~$3-8/mês |
| VPS Hostinger | ~$4-10/mês |
| Taxas Hyperliquid | 0.045% por trade |
| **Total fixo** | **~$7-18/mês** |

---

## ⚠️ Aviso de risco

Trading de derivativos com alavancagem envolve **risco elevado de perda**. Este bot é uma ferramenta de automação — não uma garantia de lucro. Nunca invista mais do que pode perder. Teste extensivamente no testnet antes de usar capital real.
