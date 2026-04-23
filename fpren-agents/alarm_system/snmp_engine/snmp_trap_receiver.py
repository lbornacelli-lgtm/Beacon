"""
snmp_trap_receiver.py — FPREN SNMP Trap Receiver

Listens for inbound SNMPv1/v2c trap PDUs on UDP port 162 using pysnmp 7.x.
Stores each trap to MongoDB trap_log and posts a raise event to alarm_events.

Usage:
    python3 snmp_trap_receiver.py

Requires root or CAP_NET_BIND_SERVICE to bind UDP 162, OR use iptables:
    sudo iptables -t nat -A PREROUTING -p udp --dport 162 -j REDIRECT --to-port 1162
    then set SNMP_TRAP_PORT=1162.
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

from pymongo import MongoClient

_HERE = os.path.dirname(os.path.abspath(__file__))
_ALARM_SYS = os.path.dirname(_HERE)
if _ALARM_SYS not in sys.path:
    sys.path.insert(0, _ALARM_SYS)

from alarm_engine import post_event

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
TRAP_PORT = int(os.environ.get("SNMP_TRAP_PORT", "162"))
SOURCE    = "snmp_trap_receiver"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SNMP-TRAP] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def _trap_severity(generic_trap: int) -> str:
    # 0=coldStart, 1=warmStart, 2=linkDown, 3=linkUp, 4=authFailure
    if generic_trap == 2:  return "Major"
    if generic_trap == 4:  return "Minor"
    if generic_trap in (0, 1): return "Warning"
    return "Warning"


class TrapReceiver:
    def __init__(self):
        self.client   = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        self.db       = self.client["weather_rss"]
        self.trap_log = self.db["trap_log"]
        self.trap_log.create_index([("received_at", -1)])
        self.trap_log.create_index([("agent_addr", 1)])
        log.info("TrapReceiver initialized — UDP %d", TRAP_PORT)

    def _cb(self, snmpEngine, stateReference, contextEngineId, contextName, varBinds, cbCtx):
        """Callback invoked by pysnmp for each received notification."""
        try:
            from pysnmp.entity.rfc3413 import ntfrcv
            transportDomain, transportAddress, securityModel, securityLevel, \
                securityName, contextEngineId, contextName, pduVersion, \
                PDU, maxSizeResponseScopedPDU, stateReference = \
                snmpEngine.msgAndPduDsp.getDispatcherContext(stateReference)
        except Exception:
            transportAddress = ("unknown",)

        agent_addr = str(transportAddress[0]) if transportAddress else "unknown"
        received   = datetime.now(timezone.utc)
        vb_list    = []
        generic_trap  = 6   # enterpriseSpecific by default
        specific_trap = 0

        for oid, val in varBinds:
            oid_str = str(oid)
            val_str = val.prettyPrint()
            vb_list.append({"oid": oid_str, "value": val_str})
            # SNMPv1 trap type is in snmpTrapType OID
            if oid_str.endswith(".1.3.6.1.6.3.1.1.4.1.0"):
                try:
                    generic_trap = int(val_str.split(".")[-1])
                except ValueError:
                    pass

        severity = _trap_severity(generic_trap)
        name     = f"SNMP Trap from {agent_addr} (type={generic_trap})"
        detail   = (f"Agent: {agent_addr}\nTime: {received.isoformat()}\n\n"
                    + "\n".join(f"  {v['oid']} = {v['value']}" for v in vb_list[:20]))

        self.trap_log.insert_one({
            "agent_addr":    agent_addr,
            "generic_trap":  generic_trap,
            "specific_trap": specific_trap,
            "var_binds":     vb_list,
            "received_at":   received,
        })

        post_event(self.db, "raise", SOURCE, name,
                   severity=severity, detail=detail,
                   remediation=(f"Investigate SNMP trap source: {agent_addr}\n"
                                "Check device logs and OIDs above."),
                   tags=["snmp", "trap", agent_addr])
        log.info("Trap from %s generic=%d (%d binds)", agent_addr, generic_trap, len(vb_list))

    def run(self):
        try:
            from pysnmp.entity import engine, config
            from pysnmp.carrier.asyncio.dgram import udp
            from pysnmp.entity.rfc3413 import ntfrcv
        except ImportError as exc:
            log.critical("pysnmp import failed: %s — pip install pysnmp", exc)
            sys.exit(1)

        snmpEngine = engine.SnmpEngine()

        # v1/v2c community
        config.add_transport(
            snmpEngine,
            udp.DOMAIN_NAME,
            udp.UdpAsyncioTransport().openServerMode(("0.0.0.0", TRAP_PORT)),
        )
        config.addV1System(snmpEngine, "trap-community", "public")
        config.addV1System(snmpEngine, "fpren-traps", "fpren_monitor")

        # Accept traps from all sources
        config.addTrapUser(snmpEngine, "trap-community")
        config.addTrapUser(snmpEngine, "fpren-traps")

        ntfrcv.NotificationReceiver(snmpEngine, self._cb)

        log.info("Listening for SNMP traps on 0.0.0.0:%d", TRAP_PORT)
        try:
            snmpEngine.transportDispatcher.runDispatcher()
        except KeyboardInterrupt:
            log.info("Trap receiver stopped.")
        finally:
            snmpEngine.transportDispatcher.closeDispatcher()


if __name__ == "__main__":
    TrapReceiver().run()
