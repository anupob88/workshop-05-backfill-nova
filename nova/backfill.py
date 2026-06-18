#!/usr/bin/env python3
"""
Nova Backfill Collector — Discord Message Backfill System
Saves messages to psi/backfill/{channel_id}/{date}/{message_id}.json

Part of Workshop 05: Backfill Midterm
Nova Oracle — 2026-06-19
"""

import json, os, sys, hashlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKFILL_ROOT = ROOT / "agents" / "data" / "psi" / "backfill"
INDEX_FILE = BACKFILL_ROOT / "index.json"


def load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"channels": {}, "total_messages": 0, "last_backfill": None}


def save_index(idx: dict):
    BACKFILL_ROOT.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")


def sanitize_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def save_message(msg: dict, channel_id: str, channel_name: str = "unknown"):
    """Save a single Discord message to psi/backfill/"""
    ts = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
    date_str = ts.strftime("%Y-%m-%d")
    
    msg_dir = BACKFILL_ROOT / sanitize_filename(channel_id) / date_str
    msg_dir.mkdir(parents=True, exist_ok=True)
    
    # Save individual message
    msg_file = msg_dir / f"{msg['id']}.json"
    if not msg_file.exists():
        msg_file.write_text(json.dumps(msg, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # Append to daily batch
    daily_file = msg_dir / "_daily.json"
    daily_msgs = []
    if daily_file.exists():
        daily_msgs = json.loads(daily_file.read_text(encoding="utf-8"))
    
    # Check for duplicates
    existing_ids = {m["id"] for m in daily_msgs}
    if msg["id"] not in existing_ids:
        daily_msgs.append(msg)
        daily_msgs.sort(key=lambda m: m["timestamp"])
        daily_file.write_text(json.dumps(daily_msgs, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    return False


def backfill_messages(messages: list, channel_id: str, channel_name: str = "unknown") -> dict:
    """Backfill a batch of messages, return stats"""
    idx = load_index()
    saved = 0
    skipped = 0
    
    for msg in messages:
        if save_message(msg, channel_id, channel_name):
            saved += 1
        else:
            skipped += 1
    
    # Update index
    cid = sanitize_filename(channel_id)
    if cid not in idx["channels"]:
        idx["channels"][cid] = {
            "name": channel_name,
            "first_seen": None,
            "last_seen": None,
            "message_count": 0
        }
    
    ch = idx["channels"][cid]
    ch["message_count"] = idx["total_messages"] + saved
    idx["total_messages"] += saved
    idx["last_backfill"] = datetime.now(timezone.utc).isoformat()
    
    # Update first/last seen
    if messages:
        timestamps = [m["timestamp"] for m in messages]
        if not ch["first_seen"] or min(timestamps) < ch["first_seen"]:
            ch["first_seen"] = min(timestamps)
        if not ch["last_seen"] or max(timestamps) > ch["last_seen"]:
            ch["last_seen"] = max(timestamps)
    
    save_index(idx)
    return {"saved": saved, "skipped": skipped, "total": idx["total_messages"]}


def stats() -> dict:
    """Return backfill statistics"""
    idx = load_index()
    return {
        "total_messages": idx["total_messages"],
        "channels": len(idx["channels"]),
        "last_backfill": idx["last_backfill"],
        "channel_list": [
            {"id": cid, "name": ch["name"], "count": ch["message_count"]}
            for cid, ch in idx["channels"].items()
        ]
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: backfill.py <stats|import> [args...]")
        print("  stats              Show backfill statistics")
        print("  import <json_file>  Import messages from JSON file")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "stats":
        s = stats()
        print(f"\n=== Nova Backfill Stats ===")
        print(f"Total messages: {s['total_messages']}")
        print(f"Channels: {s['channels']}")
        print(f"Last backfill: {s['last_backfill']}")
        print(f"\nChannels:")
        for ch in s["channel_list"]:
            print(f"  {ch['name']} ({ch['id']}): {ch['count']} msgs")
    
    elif cmd == "import":
        if len(sys.argv) < 3:
            print("Usage: backfill.py import <json_file> [channel_id] [channel_name]")
            sys.exit(1)
        
        import_file = Path(sys.argv[2])
        if not import_file.exists():
            print(f"File not found: {import_file}")
            sys.exit(1)
        
        data = json.loads(import_file.read_text(encoding="utf-8"))
        msgs = data if isinstance(data, list) else [data]
        channel_id = sys.argv[3] if len(sys.argv) > 3 else "imported"
        channel_name = sys.argv[4] if len(sys.argv) > 4 else channel_id
        
        result = backfill_messages(msgs, channel_id, channel_name)
        print(f"Backfill complete: {result['saved']} new, {result['skipped']} skipped")
        print(f"Total in index: {result['total']}")
    
    else:
        print(f"Unknown command: {cmd}")
