# CLAUDE.md — fpren-agents

Agent orchestration layer for FPREN.  See root [`CLAUDE.md`](../CLAUDE.md) for
system context and the unified AI client documentation.

---

## Overview

`fpren-agents/` contains two things:

1. **Director + Specialized Agents** — `director.py` polls MongoDB collections and
   dispatches documents to typed agents (`WeatherAgent`, `AlertsAgent`, `TrafficAgent`,
   `TTSAgent`).  All agents inherit `BaseAgent`.

2. **Alarm System** — end-to-end SNMP/watchdog/notification stack.  See
   [`alarm_system/`](alarm_system/) for its own documentation.

> The director currently has **no systemd service** and does not run automatically.
> To run it: `cd ~/Fpren-main && source venv/bin/activate && python3 fpren-agents/director.py`
> A service file (`systemd/fpren-director.service`) should be created before enabling it.

---

## Directory Layout

```
fpren-agents/
├── director.py          — polls MongoDB, dispatches to agents, posts alarms on failure
├── agents/
│   ├── base_agent.py    — abstract base: llm(), classify(), save(), queue_tts()
│   ├── weather_agent.py — handles airport_metar documents
│   ├── alerts_agent.py  — handles nws_alerts and airport_delays documents
│   ├── traffic_agent.py — handles fl_traffic documents
│   └── tts_agent.py     — handles tts_queue documents
├── prompts/             — Markdown system prompt templates loaded by BaseAgent.llm()
│   ├── alerts.md
│   ├── weather.md
│   ├── traffic.md
│   └── common.md
├── litellm_router.py    — compatibility shim over ai_client.py (do not expand)
├── routing_adapter.py   — writes routing_config.json for dynamic model tier overrides
├── execution_logger.py  — @log_agent_call decorator + patch_litellm_router() for timing
├── prompt_tuner.py      — utility for offline prompt evaluation
├── r_bridge.py          — R integration helpers
├── r_modules/           — R scripts called by agents
├── alarm_system/        — SNMP + watchdog + alarm engine (see alarm_system/CLAUDE.md)
└── logs/                — agent runtime logs
```

---

## Director

`director.py` watches these MongoDB collections (5-second poll):

| Collection | Agent dispatched |
|---|---|
| `nws_alerts` | `AlertsAgent` |
| `nws_alerts_extended` | `AlertsAgent` |
| `airport_delays` | `AlertsAgent` |
| `airport_metar` | `WeatherAgent` |
| `fl_traffic` | `TrafficAgent` |
| `tts_queue` | `TTSAgent` |

On repeated agent failure (≥3 retries on the same document):
- 3–4 failures → Minor alarm posted to `alarm_events`
- ≥5 failures → Major alarm posted to `alarm_events`

---

## BaseAgent

All agents inherit from `BaseAgent` (`agents/base_agent.py`).

```python
class MyAgent(BaseAgent):
    collection_out = "my_output_collection"

    def handle(self, doc: dict):
        # doc["_source_collection"] set by director
        result = self.llm("weather.md", user_text, size="medium")
        self.save({"result": result, ...})
        self.queue_tts(result)
```

Key methods:

| Method | Description |
|---|---|
| `llm(system, user_text, size, max_tokens)` | Chat call — `system` may be a `.md` filename or plain string |
| `classify(text)` | JSON classification via small model — returns `{category, severity, tts_priority}` |
| `save(data)` | Insert dict into `self.collection_out` |
| `queue_tts(text, voice_category, priority)` | Push to `tts_queue` collection |

---

## Prompt Templates (`prompts/`)

Templates are plain Markdown files.  Pass the filename to `self.llm()`:

```python
result = self.llm("alerts.md", alert_text)
result = self.llm("weather.md", obs_text, size="large")
```

`litellm_router.load_prompt()` and `BaseAgent.llm()` both resolve paths relative
to `fpren-agents/prompts/`.

---

## litellm_router.py (shim — do not expand)

Previously called the `litellm` library directly with hardcoded model names.
Now a thin wrapper over `weather_station.services.ai_client`.  Kept only for
`director.py` and `execution_logger.py` compatibility.

**Do not add new functionality here.**  New agents should import from
`weather_station.services.ai_client` directly.

---

## routing_adapter.py

Writes `fpren-agents/routing_config.json` with dynamic model tier overrides
(e.g. demote `large` → `medium` if the 120b model is slow).  Loaded by
`litellm_router.reload_routing()` at startup.

---

## Common Commands

```bash
cd ~/Fpren-main && source venv/bin/activate

# Run director manually (no systemd service yet)
python3 fpren-agents/director.py

# Test an agent in isolation
python3 -c "
import sys; sys.path.insert(0,'.')
from fpren-agents.agents.alerts_agent import AlertsAgent
a = AlertsAgent()
a.handle({'event': 'Tornado Warning', 'area_desc': 'Alachua County'})
"

# Check director alarm integration
mongosh weather_rss --eval "db.alarm_events.find({source:'director'}).sort({created_at:-1}).limit(5).toArray()"
```

---

## Gotchas

- Director has no systemd service — it does not run automatically.
- `BaseAgent.llm()` resolves `.md` paths relative to `fpren-agents/prompts/` — use
  just the filename, not a full path.
- `classify()` uses the **small** model (8b) with a JSON-only system prompt; it does
  not fall back gracefully if the model returns malformed JSON (returns default dict).
- `execution_logger.patch_litellm_router()` monkey-patches the router for timing;
  called once in `Director.__init__()`.
