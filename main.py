#!/usr/bin/env python3
"""
main.py — Ponto de entrada do HyperTrader.

Princípio Clean Code: "O main deve apenas inicializar e delegar."
Não há lógica aqui — apenas wiring entre componentes.
"""
import asyncio
import json
import logging
import os
import sys

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/hypertrader.log"),
        logging.StreamHandler(),
    ],
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)

from hypertrader.agent import HyperTraderAgent


def run_auto(agent: HyperTraderAgent):
    """Modo autônomo com Telegram."""
    async def _main():
        from hypertrader import config
        if config.TELEGRAM_TOKEN:
            from telegram_bot import TelegramBot
            tg = TelegramBot(agent, config.TELEGRAM_TOKEN)
            agent.telegram_bot = tg
            await tg.app.initialize()
            await tg.app.start()
            await tg.app.updater.start_polling(drop_pending_updates=True)
            try:
                await agent.run_autonomous()
            finally:
                await tg.app.updater.stop()
                await tg.app.stop()
                await tg.app.shutdown()
        else:
            await agent.run_autonomous()

    asyncio.run(_main())


def run_interactive(agent: HyperTraderAgent):
    """Modo interativo via linha de comando."""
    print("HyperTrader CLI — digite 'sair' para encerrar")
    while True:
        try:
            user_input = input("Você: ").strip()
            if not user_input or user_input.lower() in ("sair", "exit"):
                break
            elif user_input.lower().startswith("analisar "):
                symbol = user_input.split()[-1].upper()
                result = agent.analyze_market(symbol)
                print(f"\n{result['analysis']}\n")
                sig = result.get("signal")
                if sig and sig.get("signal") != "WAIT":
                    agent.execute_trade(symbol, sig)
            elif user_input.lower() == "posicoes":
                print(json.dumps(agent.active_positions, indent=2, default=str))
            else:
                print(f"\nAgente: {agent._claude.ask(user_input, symbol='_chat')}\n")
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    mode  = sys.argv[1] if len(sys.argv) > 1 else "interactive"
    agent = HyperTraderAgent()

    if mode == "auto":
        run_auto(agent)
    else:
        run_interactive(agent)
