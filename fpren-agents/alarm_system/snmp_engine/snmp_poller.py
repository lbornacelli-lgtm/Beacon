"""
snmp_poller.py — FPREN SNMP Device Poller

Polls devices listed in MongoDB snmp_devices collection using pysnmp 7.x.
Supports SNMPv1, SNMPv2c, and SNMPv3 (authPriv / authNoPriv / noAuthNoPriv).

For each polled OID value, evaluates against alarm_rules and posts events
to the alarm_events queue.

Device document schema (snmp_devices collection):
{
  "device_id":   "icecast-host",
  "host":        "127.0.0.1",
  "port":        161,
  "version":     "v2c",             # v1 | v2c | v3
  "community":   "fpren_monitor",   # v1 / v2c only
  "v3_user":     "",                # v3 only
  "v3_auth_key": "",
  "v3_priv_key": "",
  "v3_auth_proto": "SHA",          # MD5 | SHA
  "v3_priv_proto": "AES",          # DES | AES
  "v3_security_level": "authPriv", # noAuthNoPriv | authNoPriv | authPriv
  "oid_trees":   [".1.3.6.1.2.1.1", ".1.3.6.1.4.1.64533.1"],
  "poll_interval_seconds": 60,
  "enabled": true,
  "last_polled": null,
  "tags": ["icecast", "fpren"]
}
"""
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

from pymongo import MongoClient

_HERE = os.path.dirname(os.path.abspath(__file__))
_ALARM_SYS = os.path.dirname(_HERE)
if _ALARM_SYS not in sys.path:
    sys.path.insert(0, _ALARM_SYS)

from alarm_engine import post_event
from alarm_rules import RuleEngine

MONGO_URI  = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
LOOP_SLEEP = 10
SOURCE     = "snmp_poller"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SNMP-POLL] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


# ── pysnmp 7.x credential builders ───────────────────────────────────────────

def _make_credentials(dev: dict):
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData, UsmUserData,
        usmHMACMD5AuthProtocol, usmHMACSHAAuthProtocol,
        usmDESPrivProtocol, usmAesCfb128Protocol,
        usmNoAuthProtocol, usmNoPrivProtocol,
    )
    version = dev.get("version", "v2c").lower()
    if version in ("v1", "v2c"):
        mp = 0 if version == "v1" else 1
        return CommunityData(dev.get("community", "public"), mpModel=mp)

    # SNMPv3
    level = dev.get("v3_security_level", "noAuthNoPriv")
    auth_map = {"MD5": usmHMACMD5AuthProtocol, "SHA": usmHMACSHAAuthProtocol}
    priv_map = {"DES": usmDESPrivProtocol, "AES": usmAesCfb128Protocol}
    auth_proto = auth_map.get(dev.get("v3_auth_proto", "SHA"), usmHMACSHAAuthProtocol)
    priv_proto = priv_map.get(dev.get("v3_priv_proto", "AES"), usmAesCfb128Protocol)

    if level == "authPriv":
        return UsmUserData(dev["v3_user"],
                           authKey=dev.get("v3_auth_key", ""),
                           privKey=dev.get("v3_priv_key", ""),
                           authProtocol=auth_proto, privProtocol=priv_proto)
    elif level == "authNoPriv":
        return UsmUserData(dev["v3_user"],
                           authKey=dev.get("v3_auth_key", ""),
                           authProtocol=auth_proto, privProtocol=usmNoPrivProtocol)
    else:
        return UsmUserData(dev["v3_user"],
                           authProtocol=usmNoAuthProtocol, privProtocol=usmNoPrivProtocol)


# ── pysnmp 7.x async walk ─────────────────────────────────────────────────────

async def _walk_oid_async(host: str, port: int, credentials, oid_tree: str) -> list:
    from pysnmp.hlapi.v3arch.asyncio import (
        SnmpEngine, UdpTransportTarget, ContextData,
        ObjectType, ObjectIdentity, walk_cmd,
    )
    results = []
    snmp_engine = SnmpEngine()
    try:
        async for errorIndication, errorStatus, errorIndex, varBinds in walk_cmd(
            snmp_engine,
            credentials,
            await UdpTransportTarget.create((host, port), timeout=5, retries=2),
            ContextData(),
            ObjectType(ObjectIdentity(oid_tree)),
            lexicographicMode=False,
        ):
            if errorIndication:
                log.warning("Walk error %s %s: %s", host, oid_tree, errorIndication)
                break
            if errorStatus:
                log.warning("Walk status %s: %s", host, errorStatus.prettyPrint())
                break
            for vb in varBinds:
                results.append((str(vb[0]), vb[1].prettyPrint()))
    finally:
        snmp_engine.close_dispatcher()
    return results


async def _get_oid_async(host: str, port: int, credentials, oid: str) -> str | None:
    from pysnmp.hlapi.v3arch.asyncio import (
        SnmpEngine, UdpTransportTarget, ContextData,
        ObjectType, ObjectIdentity, get_cmd,
    )
    snmp_engine = SnmpEngine()
    try:
        errorIndication, errorStatus, _, varBinds = await get_cmd(
            snmp_engine,
            credentials,
            await UdpTransportTarget.create((host, port), timeout=5, retries=2),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        if errorIndication or errorStatus:
            return None
        for vb in varBinds:
            return vb[1].prettyPrint()
    except Exception:
        return None
    finally:
        snmp_engine.close_dispatcher()
    return None


# ── Rule evaluation ───────────────────────────────────────────────────────────

def _evaluate_rules(db, rules: list, device_id: str, host: str, oid: str, value_str: str):
    for rule in rules:
        rule_oid = rule.get("oid", "").lstrip(".")
        if not oid.lstrip(".").startswith(rule_oid):
            continue
        try:
            val = float(value_str)
        except (ValueError, TypeError):
            continue
        threshold = float(rule.get("threshold", 0))
        comp      = rule.get("comparison", "gt")
        triggered = (
            (comp == "gt"  and val > threshold) or
            (comp == "lt"  and val < threshold) or
            (comp == "eq"  and val == threshold) or
            (comp == "ne"  and val != threshold) or
            (comp == "gte" and val >= threshold) or
            (comp == "lte" and val <= threshold)
        )
        alarm_name = f"SNMP: {rule.get('name', oid)} [{device_id}]"
        if triggered:
            post_event(db, "raise", SOURCE, alarm_name,
                       severity=rule.get("severity", "Warning"),
                       detail=(f"Device {device_id} ({host}) OID {oid} = {value_str} "
                               f"({comp} {threshold})"),
                       remediation=rule.get("remediation", ""),
                       tags=["snmp", device_id])
        else:
            post_event(db, "clear", SOURCE, alarm_name)


# ── Main poller ───────────────────────────────────────────────────────────────

class SNMPPoller:
    def __init__(self):
        self.client  = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        self.db      = self.client["weather_rss"]
        self.devices = self.db["snmp_devices"]
        self.rules   = RuleEngine(self.db)
        self._seed_defaults()
        log.info("SNMPPoller initialized.")

    def _seed_defaults(self):
        if self.devices.count_documents({}) == 0:
            self.devices.insert_one({
                "device_id":   "fpren-vm-localhost",
                "host":        "127.0.0.1",
                "port":        161,
                "version":     "v2c",
                "community":   "fpren_monitor",
                "oid_trees":   [
                    ".1.3.6.1.2.1.1",      # system MIB
                    ".1.3.6.1.4.1.2021.10", # UCD load avg
                    ".1.3.6.1.4.1.2021.9",  # UCD disk
                    ".1.3.6.1.4.1.64533.1", # FPREN enterprise
                ],
                "poll_interval_seconds": 60,
                "enabled": True,
                "last_polled": None,
                "tags": ["fpren", "vm"],
                "created_at": datetime.now(timezone.utc),
            })
            log.info("Seeded default SNMP device (fpren-vm-localhost).")

    async def _poll_device(self, dev: dict):
        host      = dev["host"]
        port      = dev.get("port", 161)
        device_id = dev["device_id"]

        try:
            credentials = _make_credentials(dev)
        except Exception as exc:
            log.error("Credential error for %s: %s", device_id, exc)
            return

        # Reachability check via sysDescr.0
        val = await _get_oid_async(host, port, credentials, ".1.3.6.1.2.1.1.1.0")
        alarm_unreachable = f"SNMP Device Unreachable: {device_id}"
        if val is None:
            post_event(self.db, "raise", SOURCE, alarm_unreachable,
                       severity="Major",
                       detail=f"Device {device_id} ({host}:{port}) not responding.",
                       remediation=f"ping {host} — check UDP 161 firewall and SNMP community.",
                       tags=["snmp", device_id, "unreachable"])
            return
        post_event(self.db, "clear", SOURCE, alarm_unreachable)

        snmp_rules   = self.rules.get_snmp_rules()
        poll_results = []

        for tree in dev.get("oid_trees", []):
            try:
                rows = await _walk_oid_async(host, port, credentials, tree)
                for (oid, val_s) in rows:
                    poll_results.append({"oid": oid, "value": val_s})
                    _evaluate_rules(self.db, snmp_rules, device_id, host, oid, val_s)
            except Exception as exc:
                log.error("Walk error device=%s tree=%s: %s", device_id, tree, exc)

        self.db["snmp_poll_results"].insert_one({
            "device_id":    device_id,
            "host":         host,
            "polled_at":    datetime.now(timezone.utc),
            "result_count": len(poll_results),
            "results":      poll_results[:500],
        })
        self.devices.update_one(
            {"device_id": device_id},
            {"$set": {"last_polled": datetime.now(timezone.utc)}},
        )
        log.info("Polled %s — %d OIDs", device_id, len(poll_results))

    async def _run_async(self):
        log.info("SNMPPoller async loop running — sleep %ds between schedule checks", LOOP_SLEEP)
        while True:
            now = datetime.now(timezone.utc)
            for dev in self.devices.find({"enabled": True}):
                interval = dev.get("poll_interval_seconds", 60)
                last     = dev.get("last_polled")
                # MongoDB returns naive UTC datetimes — make timezone-aware for comparison
                if last is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if last is None or (now - last).total_seconds() >= interval:
                    try:
                        await self._poll_device(dev)
                    except Exception as exc:
                        log.error("Device poll error [%s]: %s", dev.get("device_id"), exc)
            await asyncio.sleep(LOOP_SLEEP)

    def run(self):
        asyncio.run(self._run_async())


if __name__ == "__main__":
    SNMPPoller().run()
