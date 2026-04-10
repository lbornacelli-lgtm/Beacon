"""
litellm_router.py  —  FPREN LiteLLM Model Router
Routes tasks to the right-sized UF HiPerGator model.
  small  → llama-3.1-8b   (classify, tag)
  medium → llama-3.3-70b  (summarize, bulletins)
  large  → nemotron-120b  (complex reports)
"""
import os
import litellm
from pathlib import Path

UF_BASE_URL = os.getenv("UF_LITELLM_BASE_URL", "https://api.ai.it.ufl.edu/v1")
UF_API_KEY  = os.getenv("UF_LITELLM_API_KEY", "")

MODEL_MAP = {
    "small":  "openai/llama-3.1-8b-instruct",
    "medium": "openai/llama-3.3-70b-instruct",
    "large":  "openai/nemotron-3-super-120b-a12b",
}

litellm.api_base = UF_BASE_URL
litellm.api_key  = UF_API_KEY

import os
os.environ["OPENAI_API_KEY"]  = UF_API_KEY
os.environ["OPENAI_API_BASE"] = UF_BASE_URL

def load_prompt(md_file):
    path = Path(__file__).parent / "prompts" / md_file
    return path.read_text(encoding="utf-8")

def complete(system_md, user_message, size="medium", max_tokens=512, temperature=0.2):
    system_content = load_prompt(system_md) if system_md.endswith(".md") else system_md
    model = MODEL_MAP.get(size, MODEL_MAP["medium"])
    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user",   "content": user_message},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        api_base=UF_BASE_URL,
        api_key=UF_API_KEY,
    )
    return resp.choices[0].message.content.strip()

def classify(text):
    import json
    system = (
        "You are a classifier for emergency/weather/traffic broadcasts. "
        "Return ONLY a JSON object with keys: "
        "category (weather|traffic|alerts|other), severity (low|medium|high), "
        "tts_priority (true|false). No extra text."
    )
    raw = complete(system, text, size="small", max_tokens=80, temperature=0.0)
    try:
        return json.loads(raw)
    except Exception:
        return {"category": "other", "severity": "low", "tts_priority": False}
