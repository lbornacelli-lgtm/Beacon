import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import db
import tts


def _elem_to_dict(elem):
    """Recursively convert an XML element to a dict."""
    d = {child.tag: child.text or "" for child in elem}
    if elem.text and elem.text.strip() and not d:
        return elem.text.strip()
    return d


def parse_json(path: Path) -> list:
    with open(path, "r") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    return data


def parse_xml(path: Path) -> list:
    tree = ET.parse(path)
    root = tree.getroot()
    records = []
    for child in root:
        records.append(_elem_to_dict(child))
    # If no children, treat root itself as a single record
    if not records:
        records = [_elem_to_dict(root)]
    return records


def import_file(path: Path) -> dict:
    """Parse a JSON or XML file, insert records, auto-convert descriptions to WAV."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        records = parse_json(path)
    elif suffix == ".xml":
        records = parse_xml(path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Use .json or .xml")

    # Stamp each record
    for r in records:
        r.setdefault("_imported_at", datetime.now(timezone.utc).isoformat())
        r.setdefault("_wav_file", None)

    ids = db.insert_entries(records)

    converted, failed = 0, 0
    for eid, record in zip(ids, records):
        record["_id"] = eid
        if record.get("description"):
            try:
                wav = tts.convert_entry(record)
                db.update_wav(eid, wav)
                converted += 1
            except Exception as e:
                print(f"TTS failed for {eid}: {e}")
                failed += 1

    return {
        "imported": len(ids),
        "converted": converted,
        "failed": failed,
    }
