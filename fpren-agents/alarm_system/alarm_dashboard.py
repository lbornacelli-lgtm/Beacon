"""
alarm_dashboard.py — FPREN Alarm Dashboard Flask Blueprint

Mounts at /alarms on the existing Flask app (port 5000).

Routes:
  GET  /alarms/                     — main dashboard (active alarms + counts)
  GET  /alarms/history              — alarm history table
  GET  /alarms/<id>                 — alarm detail page
  POST /alarms/<id>/acknowledge     — acknowledge an alarm
  POST /alarms/<id>/clear           — manually clear an alarm
  GET  /alarms/api/active           — JSON list of active alarms
  GET  /alarms/api/history          — JSON alarm history
  GET  /alarms/api/stats            — JSON alarm counts by severity/source
  GET  /alarms/api/rules            — JSON list of alarm rules
  POST /alarms/api/rules            — upsert a rule
  DELETE /alarms/api/rules/<rule_id> — delete a rule
  GET  /alarms/api/devices          — JSON SNMP device list
  POST /alarms/api/devices          — add/update SNMP device
  DELETE /alarms/api/devices/<id>   — remove SNMP device
  GET  /alarms/api/maintenance      — list maintenance windows
  POST /alarms/api/maintenance      — create a maintenance window
  DELETE /alarms/api/maintenance/<id> — remove window
  POST /alarms/api/mibs/upload      — upload + load a MIB file
  GET  /alarms/api/mibs             — list loaded MIBs
  GET  /alarms/api/mibs/lookup      — look up an OID name
"""
import os
import sys
import logging
from datetime import datetime, timezone, timedelta

from flask import (
    Blueprint, jsonify, render_template, request, abort,
    redirect, url_for, flash
)
from pymongo import MongoClient, DESCENDING
from bson import ObjectId, json_util
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from alarm_rules import RuleEngine
from snmp_engine.mib_browser import load_mib, list_mibs, lookup_oid, MIB_UPLOAD_DIR

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")

log = logging.getLogger(__name__)

alarm_bp = Blueprint(
    "alarms",
    __name__,
    url_prefix="/alarms",
    template_folder="templates",
    static_folder=None,
)

SEVERITY_ORDER = {"Critical": 4, "Major": 3, "Minor": 2, "Warning": 1}


def _get_db():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    return client["weather_rss"]


def _json(obj):
    """Serialize MongoDB documents (handles ObjectId + datetime)."""
    return json.loads(json_util.dumps(obj))


def _oid_str(oid) -> str:
    return str(oid) if oid else ""


# ── Dashboard pages ───────────────────────────────────────────────────────────

@alarm_bp.route("/")
def dashboard():
    db = _get_db()
    active = list(db["alarms"].find(
        {"status": {"$in": ["active", "acknowledged"]}},
        sort=[("severity", DESCENDING), ("raised_at", DESCENDING)],
    ).limit(200))

    # Sort by severity rank descending, then raised_at descending
    active.sort(
        key=lambda a: (-SEVERITY_ORDER.get(a.get("severity", "Warning"), 0),
                       a.get("raised_at", datetime.min)),
        reverse=False,
    )

    counts = {
        "critical": sum(1 for a in active if a.get("severity") == "Critical"),
        "major":    sum(1 for a in active if a.get("severity") == "Major"),
        "minor":    sum(1 for a in active if a.get("severity") == "Minor"),
        "warning":  sum(1 for a in active if a.get("severity") == "Warning"),
        "total":    len(active),
    }

    # Compute escalation timer info (minutes since raised)
    now = datetime.now(timezone.utc)
    for alarm in active:
        raised = alarm.get("raised_at")
        if raised:
            if raised.tzinfo is None:
                raised = raised.replace(tzinfo=timezone.utc)
            alarm["age_minutes"] = int((now - raised).total_seconds() / 60)
        else:
            alarm["age_minutes"] = 0
        alarm["_id_str"] = _oid_str(alarm.get("_id"))

    return render_template(
        "alarms/dashboard.html",
        alarms=active,
        counts=counts,
        now=now,
    )


@alarm_bp.route("/history")
def history():
    db    = _get_db()
    page  = max(1, int(request.args.get("page", 1)))
    per   = 50
    skip  = (page - 1) * per

    total = db["alarms"].count_documents({"status": "cleared"})
    docs  = list(db["alarms"].find(
        {"status": "cleared"},
        sort=[("cleared_at", DESCENDING)],
    ).skip(skip).limit(per))

    archived = list(db["alarm_history"].find(
        {}, sort=[("cleared_at", DESCENDING)]
    ).skip(skip).limit(per))

    for d in docs + archived:
        d["_id_str"] = _oid_str(d.get("_id"))

    return render_template(
        "alarms/history.html",
        alarms=docs,
        archived=archived,
        total=total,
        page=page,
        per=per,
        pages=max(1, (total + per - 1) // per),
    )


@alarm_bp.route("/<alarm_id>")
def alarm_detail(alarm_id):
    db = _get_db()
    try:
        doc = db["alarms"].find_one({"_id": ObjectId(alarm_id)})
        if not doc:
            doc = db["alarm_history"].find_one({"_id": ObjectId(alarm_id)})
    except Exception:
        abort(404)
    if not doc:
        abort(404)
    doc["_id_str"] = _oid_str(doc.get("_id"))
    now = datetime.now(timezone.utc)
    raised = doc.get("raised_at")
    if raised:
        if raised.tzinfo is None:
            raised = raised.replace(tzinfo=timezone.utc)
        doc["age_minutes"] = int((now - raised).total_seconds() / 60)
    return render_template("alarms/alarm_detail.html", alarm=doc, now=now)


# ── Acknowledge / clear ───────────────────────────────────────────────────────

@alarm_bp.route("/<alarm_id>/acknowledge", methods=["POST"])
def acknowledge(alarm_id):
    db  = _get_db()
    now = datetime.now(timezone.utc)
    username = request.form.get("username", "operator")
    try:
        result = db["alarms"].update_one(
            {"_id": ObjectId(alarm_id), "status": "active"},
            {"$set": {
                "status":           "acknowledged",
                "acknowledged_at":  now,
                "acknowledged_by":  username,
                "updated_at":       now,
            }},
        )
    except Exception:
        abort(400)
    if request.is_json:
        return jsonify({"ok": result.modified_count > 0})
    return redirect(url_for("alarms.dashboard"))


@alarm_bp.route("/<alarm_id>/clear", methods=["POST"])
def clear_alarm(alarm_id):
    db  = _get_db()
    now = datetime.now(timezone.utc)
    detail = request.form.get("detail", "Manually cleared by operator.")
    try:
        result = db["alarms"].update_one(
            {"_id": ObjectId(alarm_id),
             "status": {"$in": ["active", "acknowledged"]}},
            {"$set": {
                "status":       "cleared",
                "cleared_at":   now,
                "clear_detail": detail,
                "updated_at":   now,
            }},
        )
    except Exception:
        abort(400)
    if request.is_json:
        return jsonify({"ok": result.modified_count > 0})
    return redirect(url_for("alarms.dashboard"))


# ── JSON API — alarms ─────────────────────────────────────────────────────────

@alarm_bp.route("/api/active")
def api_active():
    db = _get_db()
    docs = list(db["alarms"].find(
        {"status": {"$in": ["active", "acknowledged"]}},
        sort=[("raised_at", DESCENDING)],
    ).limit(500))
    return jsonify(_json(docs))


@alarm_bp.route("/api/history")
def api_history():
    db    = _get_db()
    since = request.args.get("since")
    q     = {"status": "cleared"}
    if since:
        try:
            q["cleared_at"] = {"$gte": datetime.fromisoformat(since)}
        except ValueError:
            pass
    docs = list(db["alarms"].find(q, sort=[("cleared_at", DESCENDING)]).limit(500))
    docs += list(db["alarm_history"].find({}, sort=[("cleared_at", DESCENDING)]).limit(200))
    return jsonify(_json(docs))


@alarm_bp.route("/api/stats")
def api_stats():
    db = _get_db()
    pipeline = [
        {"$match": {"status": {"$in": ["active", "acknowledged"]}}},
        {"$group": {"_id": {"severity": "$severity", "source": "$source"},
                    "count": {"$sum": 1}}},
    ]
    by_severity = {"Critical": 0, "Major": 0, "Minor": 0, "Warning": 0}
    by_source   = {}
    for row in db["alarms"].aggregate(pipeline):
        sev = row["_id"]["severity"]
        src = row["_id"]["source"]
        cnt = row["count"]
        by_severity[sev] = by_severity.get(sev, 0) + cnt
        by_source[src]   = by_source.get(src, 0) + cnt
    return jsonify({
        "by_severity": by_severity,
        "by_source":   by_source,
        "total_active": sum(by_severity.values()),
    })


# ── JSON API — rules ──────────────────────────────────────────────────────────

@alarm_bp.route("/api/rules", methods=["GET"])
def api_rules_list():
    db     = _get_db()
    rules  = RuleEngine(db)
    source = request.args.get("source")
    return jsonify(_json(rules.list_rules(source=source)))


@alarm_bp.route("/api/rules", methods=["POST"])
def api_rules_upsert():
    db    = _get_db()
    rules = RuleEngine(db)
    data  = request.get_json(force=True)
    if not data or not data.get("rule_id"):
        abort(400, "rule_id is required")
    rules.upsert_rule(data)
    return jsonify({"ok": True})


@alarm_bp.route("/api/rules/<rule_id>", methods=["DELETE"])
def api_rules_delete(rule_id):
    db    = _get_db()
    rules = RuleEngine(db)
    return jsonify({"ok": rules.delete_rule(rule_id)})


# ── JSON API — SNMP devices ───────────────────────────────────────────────────

@alarm_bp.route("/api/devices", methods=["GET"])
def api_devices_list():
    db   = _get_db()
    docs = list(db["snmp_devices"].find({}, {"_id": 0}).sort("device_id", 1))
    return jsonify(_json(docs))


@alarm_bp.route("/api/devices", methods=["POST"])
def api_devices_upsert():
    db   = _get_db()
    data = request.get_json(force=True)
    if not data or not data.get("device_id"):
        abort(400, "device_id is required")
    data.setdefault("enabled", True)
    data.setdefault("port", 161)
    data.setdefault("version", "v2c")
    data["updated_at"] = datetime.now(timezone.utc)
    db["snmp_devices"].update_one(
        {"device_id": data["device_id"]},
        {"$set": data},
        upsert=True,
    )
    return jsonify({"ok": True})


@alarm_bp.route("/api/devices/<device_id>", methods=["DELETE"])
def api_devices_delete(device_id):
    db     = _get_db()
    result = db["snmp_devices"].delete_one({"device_id": device_id})
    return jsonify({"ok": result.deleted_count > 0})


# ── JSON API — maintenance windows ───────────────────────────────────────────

@alarm_bp.route("/api/maintenance", methods=["GET"])
def api_maintenance_list():
    db   = _get_db()
    docs = list(db["maintenance_windows"].find({}).sort("start", 1))
    for d in docs:
        d["_id"] = str(d["_id"])
    return jsonify(_json(docs))


@alarm_bp.route("/api/maintenance", methods=["POST"])
def api_maintenance_create():
    db   = _get_db()
    data = request.get_json(force=True)
    if not data:
        abort(400)
    try:
        data["start"] = datetime.fromisoformat(data["start"].replace("Z", "+00:00"))
        data["end"]   = datetime.fromisoformat(data["end"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        abort(400, "start and end ISO8601 datetimes are required")
    data.setdefault("source", "*")
    data.setdefault("created_by", "operator")
    data["created_at"] = datetime.now(timezone.utc)
    result = db["maintenance_windows"].insert_one(data)
    return jsonify({"ok": True, "id": str(result.inserted_id)})


@alarm_bp.route("/api/maintenance/<window_id>", methods=["DELETE"])
def api_maintenance_delete(window_id):
    db = _get_db()
    try:
        result = db["maintenance_windows"].delete_one({"_id": ObjectId(window_id)})
    except Exception:
        abort(400)
    return jsonify({"ok": result.deleted_count > 0})


# ── JSON API — MIBs ───────────────────────────────────────────────────────────

@alarm_bp.route("/api/mibs/upload", methods=["POST"])
def api_mibs_upload():
    if "mib_file" not in request.files:
        abort(400, "mib_file field required")
    f    = request.files["mib_file"]
    dest = os.path.join(MIB_UPLOAD_DIR, f.filename)
    f.save(dest)
    try:
        db     = _get_db()
        result = load_mib(dest, db)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@alarm_bp.route("/api/mibs")
def api_mibs_list():
    db = _get_db()
    return jsonify(_json(list_mibs(db)))


@alarm_bp.route("/api/mibs/lookup")
def api_mibs_lookup():
    oid = request.args.get("oid", "")
    if not oid:
        abort(400, "oid parameter required")
    db  = _get_db()
    rec = lookup_oid(oid, db)
    return jsonify(_json(rec) if rec else {"oid": oid, "name": None})
