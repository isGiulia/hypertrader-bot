# 🚀 Deploy HyperTrader via OpenClaw (Hostinger)

## ✅ VOCÊ JÁ TEM: OpenClaw rodando na Hostinger
## 🎯 OBJETIVO: Subir o HyperTrader dentro do Docker via OpenClaw

---

## PASSO 1 — Criar o Bot Telegram (se ainda não fez)

1. Abra o Telegram → procure **@BotFather**
2. Envie `/newbot` → siga as instruções → anote o **token**
3. Para descobrir seu **chat_id**: envie qualquer mensagem para **@userinfobot**

---

## PASSO 2 — Subir os arquivos na VPS via SSH

Conecte na VPS:
```bash
ssh root@SEU_IP_HOSTINGER
```

Crie a pasta do projeto:
```bash
mkdir -p /opt/hypertrader/logs
cd /opt/hypertrader
```

### Opção A — Clonar do GitHub (recomendado)
```bash
# Repositório público:
git clone https://github.com/SEU_USUARIO/hypertrader.git .

# Repositório privado (token temporário):
git clone https://SEU_TOKEN@github.com/SEU_USUARIO/hypertrader.git .
# Após clonar, limpe o token da URL:
git remote set-url origin https://github.com/SEU_USUARIO/hypertrader.git
```

### Opção B — Upload manual (sem GitHub)
Use o **File Manager** do OpenClaw ou `scp` para enviar os arquivos:
```bash
# Do seu computador:
scp main.py telegram_bot.py hyperliquid_client.py market_analyzer.py \
    risk_manager.py requirements.txt Dockerfile docker-compose.yml \
    root@SEU_IP:/opt/hypertrader/
```

---

## PASSO 3 — Criar o arquivo .env na VPS

```bash
cd /opt/hypertrader
nano .env
```

Cole e preencha:
```
ANTHROPIC_API_KEY=sk-ant-XXXXXXXXXXXXXXXX
HL_WALLET_ADDRESS=0xSUA_CARTEIRA
HL_PRIVATE_KEY=0xSUA_CHAVE_PRIVADA
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ALLOWED_IDS=123456789
```
Salvar: `Ctrl+O` → Enter → `Ctrl+X`

---

## PASSO 4 — Deploy via OpenClaw (interface web)

1. Abra o **OpenClaw** no navegador
2. Clique em **Compose ▼** (botão canto superior direito)
3. Selecione **"Criar nova stack"** ou **"Upload Compose"**
4. Dê o nome: `hypertrader`
5. Cole ou faça upload do `docker-compose.yml`
6. Configure as variáveis de ambiente nos campos do formulário
7. Clique em **Deploy**

O OpenClaw vai fazer o build e subir o container automaticamente! ✅

---

## PASSO 5 — Verificar no OpenClaw

Após o deploy, você verá:
```
hypertrader-bot    ● Em execução
```

Para ver os logs: clique no container → **Logs**

---

## PASSO 6 — Testar o Telegram

1. Abra seu bot no Telegram
2. Envie `/start`
3. Você deve ver o menu com botões
4. Clique em **🔢 Alavancagens** para ver os limites reais da Hyperliquid
5. Clique em **📊 Status** para verificar a conexão

---

## COMANDOS TELEGRAM

| Comando | Função |
|---------|--------|
| `/start` | Menu principal com todos os botões |
| `/status` | Status do bot, conta e win rate |
| `/pnl` | P&L detalhado em tempo real |
| `/posicoes` | Posições abertas |
| `/historico` | Últimos 10 trades fechados |
| `/alavancagem` | Máximo por moeda (direto da API) |
| `/analisar BTC` | Análise + sinal de qualquer símbolo |
| `/relatorio` | Relatório completo da sessão |
| `/pausar` | Pausa o bot (não fecha posições) |
| `/retomar` | Retoma o bot |
| `/fechar BTC` | Fecha posição manualmente |
| `/config` | Configurações atuais |
| Texto livre | Conversa direta com o Claude AI |

---

## ATIVAR MODO LIVE (quando estiver pronto)

Edite o `main.py` ou as variáveis no OpenClaw:

```python
"use_testnet": False,   # mainnet
"dry_run":     False,   # ordens reais
```

Reinicie o container no OpenClaw: clique nos 3 pontinhos → **Reiniciar**

---

## ARQUITETURA COMPLETA

```
Hostinger VPS
└── OpenClaw (gerenciador Docker — já rodando)
    └── Container: hypertrader-bot
        ├── main.py              ← orquestrador + loop autônomo
        ├── telegram_bot.py      ← interface Telegram (thread paralela)
        ├── hyperliquid_client.py ← API Hyperliquid (dados + ordens)
        ├── market_analyzer.py   ← indicadores técnicos (RSI, MACD, ATR...)
        └── risk_manager.py      ← gestão de risco e sizing

Fluxo a cada 3 minutos:
  1. Busca candles + book + funding rate de cada símbolo
  2. Envia dados para Claude AI → análise técnica multi-timeframe
  3. Claude retorna sinal JSON (LONG/SHORT/WAIT + confiança + leverage)
  4. Valida risco → executa ordem na Hyperliquid
  5. Monitora SL/TP a cada ciclo
  6. Notifica Telegram em cada evento (entrada, TP1, saída, P&L update)
```

---

## ⚠️ CHECKLIST DE SEGURANÇA

- [ ] Comece SEMPRE em testnet + dry_run
- [ ] `.env` nunca vai para o GitHub (está no .gitignore)
- [ ] Seu ID está em `TELEGRAM_ALLOWED_IDS`
- [ ] Revogue o GitHub Access Token após clonar
- [ ] Teste pelo menos 48h em dry_run antes de ir live
