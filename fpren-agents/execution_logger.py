"""
execution_logger.py — FPREN Agent Execution Logger

Provides log_agent_call decorator and a litellm_router patch so every
agent.handle() call is recorded in MongoDB weather_rss.execution_log.

Fields written per call:
  agent_name, prompt_file, model_tier, latency_ms, token_count,
  output_hash, retry_count, source_collection, doc_id, status, timestamp

Usage in director.py:
    from execution_logger import log_agent_call, patch_litellm_router
    patch_litellm_router()          # call once at startup

    class Director:
        @log_agent_call
        def _dispatch(self, agent, doc):
            agent.handle(doc)
"""
import hashlib
import time
import logging
import threading
from datetime import datetime, timezone
from functools import wraps
from pymongo import MongoClient

log = logging.getLogger("ExecutionLogger")

MONGO_URI = "mongodb://localhost:27017"

_mongo = None
_col   = None


def _get_col():
    global _mongo, _col
    if _col is None:
        _mongo = MongoClient(MONGO_URI)
        _col   = _mongo["weather_rss"]["execution_log"]
        _col.create_index("timestamp")
        _col.create_index([("agent_name", 1), ("prompt_file", 1)])
    return _col


# Thread-local frame stack — supports nested dispatch calls
_ctx = threading.local()


def _push_frame(frame):
    if not hasattr(_ctx, "stack"):
        _ctx.stack = []
    _ctx.stack.append(frame)


def _pop_frame():
    stack = getattr(_ctx, "stack", [])
    return stack.pop() if stack else None


def _top_frame():
    stack = getattr(_ctx, "stack", [])
    return stack[-1] if stack else None


def patch_litellm_router():
    """
    Monkey-patch litellm_router.complete() so every LLM call writes
    model_tier, prompt_file, token_count, and output_hash into the
    active execution frame.  Call once at Director startup.
    """
    import litellm_router as router
    _orig = router.complete

    @wraps(_orig)
    def _patched(system_md, user_message, size="medium",
                 max_tokens=512, temperature=0.2):
        result = _orig(system_md, user_message, size=size,
                       max_tokens=max_tokens, temperature=temperature)
        frame = _top_frame()
        if frame is not None:
            frame["model_tier"]  = size
            frame["prompt_file"] = system_md if system_md.endswith(".md") else ""
            # Characters / 4 is a reasonable token estimate when usage object
            # is not surfaced through the router's return value.
            frame["token_count"] = len(result) // 4
            frame["output_hash"] = hashlib.sha256(result.encode()).hexdigest()[:16]
        return result

    router.complete = _patched
    log.info("litellm_router.complete() patched — execution logging active")


def log_agent_call(func):
    """
    Decorator for Director._dispatch(self, agent, doc).

    Times wall-clock latency of agent.handle(), captures LLM metadata
    written by the patched litellm_router, and inserts a record into
    weather_rss.execution_log regardless of success or failure.
    """
    @wraps(func)
    def wrapper(self, agent, doc):
        frame = {
            "agent_name":        agent.__class__.__name__,
            "model_tier":        "medium",    # overwritten by patch_litellm_router
            "prompt_file":       "",           # overwritten by patch_litellm_router
            "latency_ms":        0,
            "token_count":       0,
            "output_hash":       "",
            "retry_count":       int(doc.get("retry_count", 0)),
            "source_collection": doc.get("_source_collection", ""),
            "doc_id":            str(doc.get("_id", "")),
            "status":            "ok",
            "timestamp":         datetime.now(timezone.utc),
        }
        _push_frame(frame)
        t0 = time.monotonic()
        try:
            result = func(self, agent, doc)
            frame["status"] = "ok"
            return result
        except Exception as exc:
            frame["status"] = "error"
            frame["error"]  = str(exc)
            raise
        finally:
            frame["latency_ms"] = int((time.monotonic() - t0) * 1000)
            _pop_frame()
            try:
                _get_col().insert_one(frame)
            except Exception as ex:
                log.error("Failed to write execution_log: %s", ex)

    return wrapper
