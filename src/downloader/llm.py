"""
Simple LLM Client using LiteLLM.

Supports OpenAI and Google models.
"""

import json
import logging
import re
from dataclasses import dataclass

import litellm
from litellm import acompletion

logger = logging.getLogger(__name__)

# Quiet litellm
litellm.set_verbose = False


@dataclass
class LLMResponse:
    """Response from LLM."""
    content: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient:
    """Simple LLM client for OpenAI and Google models."""

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.0):
        # Add provider prefix for Gemini models
        if model.startswith("gemini"):
            self.model = f"gemini/{model}"
        else:
            self.model = model

        self.temperature = temperature
        logger.info(f"LLM: {self.model}")

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history: list[dict] | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Make completion request."""
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        # JSON mode for OpenAI
        if json_mode and self.model.startswith("gpt-"):
            kwargs["response_format"] = {"type": "json_object"}

        response = await acompletion(**kwargs)

        return LLMResponse(
            content=response.choices[0].message.content or "",
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
        )

    def parse_json(self, text: str) -> dict:
        """Parse JSON from response."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Extract from code blocks
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Find JSON object
        match = re.search(r'\{[^{}]*"action"[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return {"error": "Failed to parse", "raw": text[:200]}
