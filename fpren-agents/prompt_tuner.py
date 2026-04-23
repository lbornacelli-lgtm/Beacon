"""
prompt_tuner.py — FPREN Prompt Performance Tuner

Reads weather_rss.routing_scores (written by outcome_scorer.R), identifies
prompt files whose composite avg_score is below SCORE_THRESHOLD, and inserts
an HTML comment flag at the top of the affected fpren-agents/prompts/*.md.
Passing prompts have any existing flag removed.

The flag is an HTML comment (invisible when rendered, ignored by LLMs):
  <!-- FPREN-TUNING: avg_score=0.42 ... flagged 2026-04-10 -->

A JSON summary is also written to fpren-agents/prompt_tuning_report.json.

Run standalone:   python3 prompt_tuner.py
Or call:          PromptTuner().run()
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from pymongo import MongoClient

log = logging.getLogger("PromptTuner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [TUNER] %(message)s")

MONGO_URI       = "mongodb://localhost:27017"
PROMPTS_DIR     = Path(__file__).parent / "prompts"
REPORT_PATH     = Path(__file__).parent / "prompt_tuning_report.json"
SCORE_THRESHOLD = 0.60   # prompts below this avg_score are flagged
MIN_SAMPLE_SIZE = 5      # ignore entries with fewer than N calls

# Matches any existing FPREN-TUNING comment block
_FLAG_RE = re.compile(r"<!--\s*FPREN-TUNING:.*?-->", re.DOTALL)


class PromptTuner:
    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db     = self.client["weather_rss"]

    def _load_scores(self):
        """
        Aggregate routing_scores by prompt_file (multiple agent/tier rows
        may exist per file).  Returns list of:
          {prompt_file, n, avg_score, avg_latency, retry_rate}
        """
        try:
            rows = list(self.db["routing_scores"].find(
                {"prompt_file": {"$nin": ["", None]}},
                {"prompt_file": 1, "avg_score": 1, "n": 1,
                 "avg_latency": 1, "retry_rate": 1, "_id": 0}
            ))
        except Exception as exc:
            log.error("Failed to read routing_scores: %s", exc)
            return []

        by_file: dict = {}
        for row in rows:
            pf = row.get("prompt_file", "")
            if not pf:
                continue
            e = by_file.setdefault(pf, {"n": 0, "score_sum": 0.0,
                                        "latency_sum": 0.0, "retry_sum": 0.0})
            n = int(row.get("n") or 0)
            e["n"]           += n
            e["score_sum"]   += float(row.get("avg_score",   0) or 0) * n
            e["latency_sum"] += float(row.get("avg_latency", 0) or 0) * n
            e["retry_sum"]   += float(row.get("retry_rate",  0) or 0) * n

        results = []
        for pf, e in by_file.items():
            if e["n"] == 0:
                continue
            results.append({
                "prompt_file":  pf,
                "n":            e["n"],
                "avg_score":    round(e["score_sum"]   / e["n"], 4),
                "avg_latency":  round(e["latency_sum"] / e["n"], 1),
                "retry_rate":   round(e["retry_sum"]   / e["n"], 4),
            })
        return results

    @staticmethod
    def _build_flag(row):
        return (
            f"<!-- FPREN-TUNING: avg_score={row['avg_score']:.3f} "
            f"(threshold {SCORE_THRESHOLD}) | "
            f"n={row['n']} calls | "
            f"avg_latency={row['avg_latency']:.0f}ms | "
            f"retry_rate={row['retry_rate']:.1%} | "
            f"flagged {datetime.now(timezone.utc).strftime('%Y-%m-%d')} -->"
        )

    def _flag_prompt(self, md_path, row):
        text     = md_path.read_text(encoding="utf-8")
        clean    = _FLAG_RE.sub("", text).lstrip("\n")
        new_text = self._build_flag(row) + "\n" + clean
        md_path.write_text(new_text, encoding="utf-8")
        log.info("Flagged %-20s  avg_score=%.3f  n=%d",
                 md_path.name, row["avg_score"], row["n"])

    def _clear_flag(self, md_path):
        text     = md_path.read_text(encoding="utf-8")
        new_text = _FLAG_RE.sub("", text).lstrip("\n")
        if new_text != text:
            md_path.write_text(new_text, encoding="utf-8")
            log.info("Cleared flag from %s (score now passing)", md_path.name)

    def run(self):
        scores = self._load_scores()
        if not scores:
            log.info("No routing_scores data yet — nothing to tune")
            return

        score_map = {r["prompt_file"]: r for r in scores}
        flagged   = []

        for md_path in sorted(PROMPTS_DIR.glob("*.md")):
            row = score_map.get(md_path.name)
            if row is None:
                continue    # no execution data for this prompt yet
            if row["n"] < MIN_SAMPLE_SIZE:
                continue    # too few calls to judge

            if row["avg_score"] < SCORE_THRESHOLD:
                self._flag_prompt(md_path, row)
                flagged.append(row["prompt_file"])
            else:
                self._clear_flag(md_path)

        all_md = list(PROMPTS_DIR.glob("*.md"))
        log.info("Prompt tuning complete — %d/%d prompts flagged",
                 len(flagged), len(all_md))

        summary = {
            "run_at":    datetime.now(timezone.utc).isoformat(),
            "threshold": SCORE_THRESHOLD,
            "scores":    scores,
            "flagged":   flagged,
        }
        REPORT_PATH.write_text(json.dumps(summary, indent=2))
        log.info("Summary written → %s", REPORT_PATH.name)


if __name__ == "__main__":
    PromptTuner().run()
