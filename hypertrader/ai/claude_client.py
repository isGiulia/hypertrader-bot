"""
ai/claude_client.py — Comunicação com a API do Claude AI.

Princípio Clean Code: "Separe preocupações (Separation of Concerns)."
Todo retry, histórico de conversa e tratamento de erros da API ficam aqui.
O resto do sistema não sabe nada sobre como o Claude funciona.
"""
import re
import json
import time
import logging
from typing import Optional

import anthropic

from hypertrader.ai.prompts import TRADER_SYSTEM_PROMPT
from hypertrader import config

logger = logging.getLogger("ClaudeClient")

_WAIT_FALLBACK = '{"signal":"WAIT","confidence":0,"reasoning_simples":"API indisponivel"}'


class ClaudeClient:
    """
    Encapsula toda a comunicação com o Claude.
    Responsabilidades:
      - Manter histórico de conversa por símbolo
      - Retry com backoff em caso de sobrecarga (529) ou rate limit (429)
      - Extrair e parsear o JSON do sinal retornado pelo modelo
    """

    def __init__(self, notify_fn=None):
        self._ai      = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self._history: dict = {}
        self._notify  = notify_fn or (lambda msg: None)

    # ── Public API

    def ask(self, prompt: str, context: dict = None, symbol: str = "_default") -> str:
        """Envia uma mensagem ao Claude e retorna a resposta em texto."""
        full_prompt = self._build_prompt(prompt, context)
        history     = self._history.setdefault(symbol, [])
        history.append({"role": "user", "content": full_prompt})

        model = config.MODEL_REPORT if symbol in ("_report", "_chat") else config.MODEL_TRADING

        for attempt in range(5):
            try:
                response = self._ai.messages.create(
                    model=model,
                    max_tokens=1024,
                    system=[{
                        "type": "text",
                        "text": TRADER_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=history,
                )
                answer = response.content[0].text
                history.append({"role": "assistant", "content": answer})
                self._trim_history(symbol)
                return answer

            except Exception as error:
                if not self._handle_error(error, attempt, symbol):
                    history.pop()
                    return _WAIT_FALLBACK

        history.pop()
        logger.error(f"API indisponivel após 5 tentativas [{symbol}]")
        self._notify("🚨 API Claude indisponivel — 5 tentativas. WAIT aplicado.")
        return _WAIT_FALLBACK

    def extract_signal(self, text: str) -> Optional[dict]:
        """Extrai o JSON de sinal da resposta do Claude."""
        for pattern in [r"```json(.*?)```", r"```(.*?)```"]:
            for match in re.findall(pattern, text, re.DOTALL):
                signal = self._try_parse(match.strip())
                if signal:
                    return signal

        signal = self._try_parse(text.strip())
        if signal:
            return signal

        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            signal = self._try_parse(text[start:end + 1])
            if signal:
                return signal

        logger.warning(f"Sinal não extraído: {text[:150]}")
        return None

    def clear_history(self, symbol: str = None):
        """Limpa histórico de conversa de um símbolo (ou todos)."""
        if symbol:
            self._history.pop(symbol, None)
        else:
            self._history.clear()

    # ── Private helpers

    def _build_prompt(self, prompt: str, context: dict) -> str:
        if context:
            return prompt + "\n\n" + json.dumps(context, indent=2, default=str)
        return prompt

    def _trim_history(self, symbol: str, max_messages: int = 20):
        history = self._history.get(symbol, [])
        if len(history) > max_messages:
            self._history[symbol] = history[-max_messages:]

    def _try_parse(self, text: str) -> Optional[dict]:
        try:
            data = json.loads(text)
            return data if "signal" in data else None
        except Exception:
            return None

    def _handle_error(self, error: Exception, attempt: int, symbol: str) -> bool:
        """
        Trata erros da API. Retorna True se deve tentar novamente,
        False se deve abandonar a tentativa.
        """
        message = str(error)

        if "529" in message or "overloaded" in message.lower():
            wait = 60 * (attempt + 1)
            logger.warning(f"API sobrecarregada t{attempt + 1}/5, aguardando {wait}s")
            self._notify(f"⏳ API sobrecarregada. Aguardando {wait}s (t{attempt + 1}/5)")
            time.sleep(wait)
            return True

        if "429" in message or "rate" in message.lower():
            wait = 30 * (attempt + 1)
            logger.warning(f"Rate limit t{attempt + 1}/5, aguardando {wait}s")
            time.sleep(wait)
            return True

        logger.error(f"Claude [{symbol}]: {error}")
        return False   # erro desconhecido — não tenta de novo
