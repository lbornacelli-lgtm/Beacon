"""
litellm_router.py — compatibility shim

This module previously called the litellm library directly with hardcoded
model names.  It now delegates to weather_station.services.ai_client so
that all AI calls share a single client, model tier configuration, and
timeout settings.

New code should import from ai_client directly.  This shim exists only to
keep director.py and routing_adapter.py working without changes.
"""
import json
import logging
import os
import sys
from pathlib import Path

# Add project root so weather_station imports resolve from fpren-agents/
_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from weather_station.services.ai_client import chat as _chat, is_configured
from weather_station.config.ai_config import (
    MODEL_SMALL, MODEL_MEDIUM, MODEL_LARGE,
)

_rlog = logging.getLogger("litellm_router")

# Preserve the MODEL_MAP interface so routing_adapter.py can still read/write it
MODEL_MAP = {
    "small":  MODEL_SMALL,
    "medium": MODEL_MEDIUM,
    "large":  MODEL_LARGE,
}

# routing_config.json support kept for routing_adapter.py compatibility
_ROUTING_CONFIG = Path(__file__).parent / "routing_config.json"


def reload_routing():
    """Re-read routing_config.json overrides (written by routing_adapter.py)."""
    if not _ROUTING_CONFIG.exists():
        return
    try:
        cfg = json.loads(_ROUTING_CONFIG.read_text())
        overrides = cfg.get("model_map", {})
        if overrides:
            MODEL_MAP.update(overrides)
            _rlog.info("Routing overrides loaded (generated %s)",
                       cfg.get("generated_at", "?"))
    except Exception as exc:
        _rlog.warning("Could not load routing_config.json: %s", exc)


reload_routing()


def load_prompt(md_file: str) -> str:
    path = Path(__file__).parent / "prompts" / md_file
    return path.read_text(encoding="utf-8")


def complete(system_md: str, user_message: str, size: str = "medium",
             max_tokens: int = 512, temperature: float = 0.2) -> str:
    """Drop-in replacement for the old litellm.completion call."""
    system_content = load_prompt(system_md) if system_md.endswith(".md") else system_md
    return _chat(user_message, system=system_content, size=size, max_tokens=max_tokens)


def classify(text: str) -> dict:
    """Drop-in replacement for the old classify() call."""
    system = (
        "You are a classifier for emergency/weather/traffic broadcasts. "
        "Return ONLY a JSON object with keys: "
        "category (weather|traffic|alerts|other), severity (low|medium|high), "
        "tts_priority (true|false). No extra text."
    )
    raw = _chat(text, system=system, size="small", max_tokens=80)
    try:
        return json.loads(raw)
    except Exception:
        return {"category": "other", "severity": "low", "tts_priority": False}
