"""
ai_config.py — FPREN AI Configuration Constants

Central location for model tier names, per-use-case token budgets, and retry
settings.  Import from here instead of scattering literals across callers.

Model tiers route to UF HiPerGator models via the LiteLLM proxy at
https://api.ai.it.ufl.edu.  Override any tier at runtime with the
corresponding environment variable (see below).
"""
import os

# ── Model tiers ────────────────────────────────────────────────────────────────
# small  → single-word classify / tag responses (fast, cheap)
# medium → rewrites, summaries, analysis (default for most calls)
# large  → complex long-form reports and synthesis

MODEL_SMALL  = os.getenv("UF_LITELLM_MODEL_SMALL",  "llama-3.1-8b-instruct")
MODEL_MEDIUM = os.getenv("UF_LITELLM_MODEL_MEDIUM",  "llama-3.3-70b-instruct")
MODEL_LARGE  = os.getenv("UF_LITELLM_MODEL_LARGE",   "nemotron-3-super-120b-a12b")

MODEL_DEFAULT = MODEL_MEDIUM   # used when no size/model is specified

# ── Per-use-case max_tokens ────────────────────────────────────────────────────

TOKENS_CLASSIFY       = 10    # single-word severity output (routine/elevated/critical)
TOKENS_REWRITE        = 120   # broadcast-ready NWS alert rewrite  (≤60 words target)
TOKENS_BROADCAST      = 200   # zone broadcast script              (≤150 words target)
TOKENS_ANALYSIS       = 250   # demographic / census / BCP narrative
TOKENS_RIVER_RESPONSE = 600   # river agent per-iteration + final response
TOKENS_AGENT_STEP     = 600   # tool-calling agent per-iteration response
TOKENS_AGENT_FINAL    = 1024  # final synthesis after all tool calls

# ── Retry / resilience ─────────────────────────────────────────────────────────

RETRY_ATTEMPTS  = 2    # total attempts (original + 1 retry)
RETRY_BACKOFF_S = 0.5  # seconds to wait between attempts
