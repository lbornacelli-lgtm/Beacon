"""Shared LiteLLM client for the FPREN weather station.

All AI calls in this project go through this module so the endpoint, key,
and model selection only need to be set in one place.

Environment variables (set in weather_station/.env):
    UF_LITELLM_BASE_URL       — LiteLLM proxy base URL
    UF_LITELLM_API_KEY        — LiteLLM virtual key (must start with sk-)
    UF_LITELLM_MODEL          — explicit model override (bypasses tier routing)
    UF_LITELLM_MODEL_SMALL    — override for the "small" tier
    UF_LITELLM_MODEL_MEDIUM   — override for the "medium" tier
    UF_LITELLM_MODEL_LARGE    — override for the "large" tier

Tier routing:
    size="small"  → llama-3.1-8b  (classify / tag)
    size="medium" → llama-3.3-70b (rewrites, summaries, analysis)  [default]
    size="large"  → nemotron-120b (complex reports, long-form)

Use chat_with_retry() for calls that should auto-retry on transient failures.
Use chat() when you need precise control (e.g. custom validation + retry loop).
"""

import logging
import os
import time

from openai import OpenAI

from weather_station.config.ai_config import (
    MODEL_SMALL, MODEL_MEDIUM, MODEL_LARGE, MODEL_DEFAULT,
    RETRY_ATTEMPTS, RETRY_BACKOFF_S,
)

logger = logging.getLogger("ai_client")

_BASE_URL = os.getenv("UF_LITELLM_BASE_URL", "https://api.ai.it.ufl.edu")
_API_KEY  = os.getenv("UF_LITELLM_API_KEY", "")
# UF_LITELLM_MODEL lets operators pin a specific model; tier routing still works
# when this is unset (the typical case).
_MODEL_OVERRIDE = os.getenv("UF_LITELLM_MODEL", "")

# Single shared client — instantiated once at first use.
_client: OpenAI | None = None

_TIER_MAP = {
    "small":  MODEL_SMALL,
    "medium": MODEL_MEDIUM,
    "large":  MODEL_LARGE,
}


def _model_for_size(size: str, explicit_model: str = "") -> str:
    """Resolve the model name to use for a call.

    Priority: explicit_model arg > UF_LITELLM_MODEL env var > tier routing.
    """
    if explicit_model:
        return explicit_model
    if _MODEL_OVERRIDE:
        return _MODEL_OVERRIDE
    return _TIER_MAP.get(size, MODEL_DEFAULT)


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not _API_KEY:
            raise RuntimeError(
                "UF_LITELLM_API_KEY is not set. "
                "Add it to weather_station/.env to enable AI features."
            )
        _client = OpenAI(base_url=_BASE_URL, api_key=_API_KEY, timeout=30.0)
        logger.info(
            "LiteLLM client ready → %s  small=%s  medium=%s  large=%s",
            _BASE_URL, MODEL_SMALL, MODEL_MEDIUM, MODEL_LARGE,
        )
    return _client


def chat(
    prompt: str,
    system: str = "",
    size: str = "medium",
    model: str = "",
    max_tokens: int = 512,
) -> str:
    """Send a single chat completion and return the response text.

    Raises RuntimeError if the API key is not configured.
    Raises openai.*Error on API failure — callers should catch and fall back.

    Args:
        prompt:     User message content.
        system:     Optional system prompt.
        size:       Model tier — "small", "medium", or "large" (default "medium").
        model:      Explicit model override (bypasses size and env var).
        max_tokens: Maximum tokens in the response.
    """
    client  = _get_client()
    resolved = _model_for_size(size, model)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=resolved,
        messages=messages,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


def chat_with_retry(
    prompt: str,
    system: str = "",
    size: str = "medium",
    model: str = "",
    max_tokens: int = 512,
    attempts: int = RETRY_ATTEMPTS,
) -> str:
    """Like chat() but retries on transient API errors.

    Retries up to `attempts` times with a short backoff between attempts.
    Raises the final exception if all attempts fail — callers should still
    catch and apply a rule-based fallback where needed.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return chat(prompt, system=system, size=size, model=model,
                        max_tokens=max_tokens)
        except Exception as exc:
            last_exc = exc
            if i < attempts - 1:
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                    i + 1, attempts, exc, RETRY_BACKOFF_S,
                )
                time.sleep(RETRY_BACKOFF_S)
    raise last_exc  # type: ignore[misc]


def is_configured() -> bool:
    """Return True if the API key env var is present."""
    return bool(_API_KEY)


def run_agent(
    system_prompt: str,
    tools: list,
    tool_functions: dict,
    initial_message: str,
    size: str = "medium",
    model: str = "",
    max_iterations: int = 10,
    max_tokens: int = 1024,
) -> dict:
    """Run a tool-calling agent loop using UF LiteLLM.

    The agent sends the initial message, receives a response, executes any
    tool calls the LLM requests, feeds results back, and repeats until the
    LLM returns a plain-text answer or max_iterations is reached.

    Args:
        system_prompt:   System instructions for the agent.
        tools:           List of tool schemas in OpenAI function-calling format.
        tool_functions:  Dict mapping function name → callable.
        initial_message: The user's task description.
        size:            Model tier — "small", "medium", or "large".
        model:           Explicit model override (bypasses size).
        max_iterations:  Safety cap on tool-calling rounds.
        max_tokens:      Max tokens per LLM response.

    Returns:
        {
            "response":   str,   # Final plain-text answer from the LLM
            "tool_calls": list,  # [{tool, args, result}, ...] audit log
            "iterations": int,   # Number of tool-calling rounds used
        }
    """
    import json as _json

    client   = _get_client()
    resolved = _model_for_size(size, model)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": initial_message},
    ]
    tool_calls_log = []
    iterations = 0

    while iterations < max_iterations:
        response = client.chat.completions.create(
            model      = resolved,
            messages   = messages,
            tools      = tools or None,
            tool_choice= "auto" if tools else "none",
            max_tokens = max_tokens,
        )
        msg = response.choices[0].message

        messages.append({
            "role":       "assistant",
            "content":    msg.content,
            "tool_calls": [
                {
                    "id":       tc.id,
                    "type":     "function",
                    "function": {"name": tc.function.name,
                                 "arguments": tc.function.arguments},
                }
                for tc in (msg.tool_calls or [])
            ] or None,
        })

        if not msg.tool_calls:
            return {
                "response":   (msg.content or "").strip(),
                "tool_calls": tool_calls_log,
                "iterations": iterations,
            }

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = _json.loads(tc.function.arguments)
            except Exception:
                fn_args = {}

            logger.debug("Agent tool call: %s(%s)", fn_name, fn_args)

            fn = tool_functions.get(fn_name)
            if fn is None:
                result = {"error": f"Unknown tool: {fn_name}"}
            else:
                try:
                    result = fn(**fn_args)
                except Exception as exc:
                    result = {"error": f"Tool error: {exc}"}

            tool_calls_log.append({"tool": fn_name, "args": fn_args, "result": result})

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      _json.dumps(result, default=str),
            })

        iterations += 1

    # Safety exit — ask for a final answer without allowing more tool calls
    final = client.chat.completions.create(
        model      = resolved,
        messages   = messages + [{"role": "user",
                                  "content": "Please provide your final answer now."}],
        max_tokens = max_tokens,
    )
    return {
        "response":   (final.choices[0].message.content or "").strip(),
        "tool_calls": tool_calls_log,
        "iterations": iterations,
    }
