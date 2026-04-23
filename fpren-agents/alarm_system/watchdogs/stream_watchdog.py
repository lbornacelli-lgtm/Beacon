"""
stream_watchdog.py — FPREN Icecast Stream Watchdog

Polls the Icecast status endpoint every 60 seconds.
Raises alarms via alarm_events queue when:
  • Primary /fpren mount is offline               → Critical
  • Zone mount is offline                         → Major
  • Bitrate drops below configured minimum        → Minor
  • Listener count anomaly (sudden spike/drop)    → Warning

Integrates with the existing scripts/stream_monitor.py logic but adds
full alarm engine integration with deduplication and severity tracking.
"""
import logging
import os
import socket
import sys
import time
from datetime import datetime, timezone

import requests
from pymongo import MongoClient

# Path resolution for sibling imports
_HERE = os.path.dirname(os.path.abspath(__file__))
_ALARM_SYS = os.path.dirname(_HERE)
if _ALARM_SYS not in sys.path:
    sys.path.insert(0, _ALARM_SYS)

from alarm_engine import post_event

MONGO_URI       = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
ICECAST_HOST    = os.environ.get("ICECAST_HOST", "127.0.0.1")
ICECAST_PORT    = int(os.environ.get("ICECAST_PORT", "8000"))
POLL_INTERVAL   = 60    # seconds
MIN_BITRATE     = 64    # kbps — alarm below this
LISTENER_SPIKE  = 50    # alarm if listeners jump/drop by more than this in one poll
SOURCE          = "stream_watchdog"

EXPECTED_MOUNTS = [
    "/fpren",
    "/north-florida",
    "/central-florida",
    "/south-florida",
    "/tampa",
    "/miami",
    "/orlando",
    "/jacksonville",
    "/gainesville",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [STREAM-WD] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


class StreamWatchdog:
    def __init__(self):
        self.client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        self.db     = self.client["weather_rss"]
        self._prev_listeners: dict[str, int] = {}
        self._prev_mounts_up: set[str] = set()
        log.info("StreamWatchdog initialized — polling every %ds", POLL_INTERVAL)

    # ── Icecast status fetch ──────────────────────────────────────────────────

    def _fetch_status(self) -> dict | None:
        url = f"http://{ICECAST_HOST}:{ICECAST_PORT}/status-json.xsl"
        try:
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("Icecast status fetch failed: %s", exc)
            # Bare TCP check to distinguish total outage
            try:
                with socket.create_connection((ICECAST_HOST, ICECAST_PORT), timeout=5):
                    pass
                return {"icestats": {"source": []}}  # port open, no JSON
            except Exception:
                return None  # total outage

    def _parse_sources(self, data: dict) -> dict:
        """Return dict of {mount_path: source_dict}."""
        icestats = data.get("icestats", {})
        raw      = icestats.get("source", [])
        if isinstance(raw, dict):
            raw = [raw]
        result = {}
        for src in raw:
            url = src.get("listenurl", "")
            mount = url.split(f":{ICECAST_PORT}", 1)[-1] or url
            result[mount] = src
        return result

    # ── Check helpers ─────────────────────────────────────────────────────────

    def _check_icecast_reachable(self, data) -> bool:
        if data is None:
            post_event(
                self.db, "raise", SOURCE, "Icecast Unreachable",
                severity="Critical",
                detail=(f"Icecast at {ICECAST_HOST}:{ICECAST_PORT} is not reachable. "
                        "Both the HTTP status endpoint and bare TCP connection failed."),
                remediation=(
                    "sudo systemctl status icecast2\n"
                    "sudo systemctl restart icecast2\n"
                    "sudo journalctl -u icecast2 -n 30"
                ),
                tags=["icecast", "stream"],
            )
            return False
        post_event(self.db, "clear", SOURCE, "Icecast Unreachable")
        return True

    def _check_mount(self, mount: str, sources: dict):
        if mount in sources:
            post_event(self.db, "clear", SOURCE, f"Mount Offline: {mount}")
            return True
        sev  = "Critical" if mount == "/fpren" else "Major"
        detail = (f"Icecast mount {mount} has no live source connected. "
                  f"Active mounts: {sorted(sources.keys()) or '(none)'}")
        post_event(
            self.db, "raise", SOURCE, f"Mount Offline: {mount}",
            severity=sev,
            detail=detail,
            remediation=(
                "sudo systemctl status fpren-multi-zone-streamer\n"
                "sudo journalctl -u fpren-multi-zone-streamer -n 30\n"
                "sudo systemctl restart fpren-multi-zone-streamer"
            ),
            tags=["icecast", "stream", mount],
        )
        return False

    def _check_bitrate(self, mount: str, src: dict):
        bitrate = src.get("bitrate")
        if bitrate is None:
            return
        try:
            kbps = float(bitrate)
        except (ValueError, TypeError):
            return
        if kbps < MIN_BITRATE:
            post_event(
                self.db, "raise", SOURCE, f"Low Bitrate: {mount}",
                severity="Minor",
                detail=f"Mount {mount} bitrate is {kbps:.0f} kbps (minimum: {MIN_BITRATE} kbps).",
                remediation=(
                    "Check FFmpeg encoder for this zone:\n"
                    "sudo journalctl -u fpren-multi-zone-streamer -n 30\n"
                    "Check audio source files in weather_station/audio/zones/"
                ),
                tags=["icecast", "bitrate", mount],
            )
        else:
            post_event(self.db, "clear", SOURCE, f"Low Bitrate: {mount}")

    def _check_listeners(self, mount: str, src: dict):
        try:
            listeners = int(src.get("listeners", 0))
        except (ValueError, TypeError):
            return
        prev = self._prev_listeners.get(mount)
        if prev is not None:
            delta = abs(listeners - prev)
            if delta >= LISTENER_SPIKE:
                direction = "spike" if listeners > prev else "drop"
                post_event(
                    self.db, "raise", SOURCE, f"Listener Count Anomaly: {mount}",
                    severity="Warning",
                    detail=(f"Mount {mount} listener count {direction}: "
                            f"{prev} → {listeners} (Δ{delta})."),
                    remediation="Monitor stream health. May indicate upstream disruption or viral event.",
                    tags=["icecast", "listeners", mount],
                )
            else:
                post_event(self.db, "clear", SOURCE, f"Listener Count Anomaly: {mount}")
        self._prev_listeners[mount] = listeners

    # ── Main poll ─────────────────────────────────────────────────────────────

    def poll(self):
        data    = self._fetch_status()
        if not self._check_icecast_reachable(data):
            return
        sources = self._parse_sources(data)
        for mount in EXPECTED_MOUNTS:
            up = self._check_mount(mount, sources)
            if up and mount in sources:
                self._check_bitrate(mount, sources[mount])
                self._check_listeners(mount, sources[mount])

    def run(self):
        log.info("StreamWatchdog running.")
        while True:
            try:
                self.poll()
            except Exception as exc:
                log.error("Poll error: %s", exc)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    StreamWatchdog().run()
