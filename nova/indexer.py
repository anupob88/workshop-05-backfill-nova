#!/usr/bin/env python3
"""
Nova Backfill Indexer — SQLite FTS5 Full-Text Search Index
Reads from psi/backfill/ JSON files, builds searchable index

Part of Workshop 05: Backfill Midterm
Nova Oracle — 2026-06-19
"""

import json, os, sys, sqlite3
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
BACKFILL_ROOT = ROOT / "agents" / "data" / "psi" / "backfill"
DB_PATH = BACKFILL_ROOT / "search.db"


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def init_db():
    """Initialize SQLite FTS5 database"""
    db = get_db()
    
    db.executescript("""
        -- Messages table (source of truth) — append-only versioned store
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            channel_id TEXT NOT NULL,
            channel_name TEXT,
            author TEXT,
            author_id TEXT,
            timestamp TEXT NOT NULL,
            content TEXT,
            has_attachments INTEGER DEFAULT 0,
            attachment_count INTEGER DEFAULT 0,
            raw_json TEXT,
            prev_version_id TEXT,
            is_tombstone INTEGER DEFAULT 0,
            PRIMARY KEY (id, version)
        );
        
        -- FTS5 virtual table for full-text search
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            channel_name,
            author,
            content,
            content='messages',
            content_rowid='rowid'
        );
        
        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, channel_name, author, content)
            VALUES (new.rowid, new.channel_name, new.author, new.content);
        END;
        
        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, channel_name, author, content)
            VALUES ('delete', old.rowid, old.channel_name, old.author, old.content);
        END;
        
        -- Index for fast queries
        CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id);
        CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author_id);
        CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
        
        -- Metadata table
        CREATE TABLE IF NOT EXISTS index_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    
    db.commit()
    return db


def index_all(reindex: bool = False) -> dict:
    """Index all messages from psi/backfill/ into SQLite"""
    db = init_db()
    
    if reindex:
        db.execute("DROP TABLE IF EXISTS messages")
        db.execute("DROP TABLE IF EXISTS messages_fts")
        db.execute("DELETE FROM index_meta")
        # Re-initialize fresh schema
        db.close()
        db = init_db()
    
    total = 0
    new = 0
    channels = {}
    
    # Walk through all backfill directories
    for channel_dir in BACKFILL_ROOT.iterdir():
        if not channel_dir.is_dir() or channel_dir.name == "search.db":
            continue
        
        channel_id = channel_dir.name
        channels[channel_id] = 0
        
        for date_dir in channel_dir.iterdir():
            if not date_dir.is_dir():
                continue
            
            daily_file = date_dir / "_daily.json"
            if not daily_file.exists():
                continue
            
            try:
                messages = json.loads(daily_file.read_text(encoding="utf-8"))
                for msg in messages:
                    total += 1
                    
                    # Append-only versioned store (inspired by Tonk — Principle 1)
                    # Check if same content already indexed
                    existing = db.execute(
                        "SELECT id, version, content FROM messages WHERE id = ? ORDER BY version DESC LIMIT 1",
                        (msg["id"],)
                    ).fetchone()

                    new_content = msg.get("content", "")

                    if existing:
                        if existing["content"] == new_content:
                            continue  # Same content, skip
                        # Content changed — insert new version
                        version = existing["version"] + 1
                        prev_id = existing["id"]
                    else:
                        version = 1
                        prev_id = None

                    new += 1
                    channels[channel_id] += 1

                    db.execute("""
                        INSERT INTO messages (id, version, channel_id, channel_name, author, author_id, timestamp, content, has_attachments, attachment_count, raw_json, prev_version_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        msg["id"],
                        version,
                        msg.get("channel_id", channel_id),
                        msg.get("channel_name", ""),
                        msg.get("author", ""),
                        msg.get("author_id", ""),
                        msg.get("timestamp", ""),
                        new_content,
                        1 if msg.get("attachments") else 0,
                        len(msg.get("attachments", [])),
                        json.dumps(msg, ensure_ascii=False),
                        prev_id
                    ))
            except Exception as e:
                print(f"  Error reading {daily_file}: {e}")
    
    db.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
               ("last_index", datetime.now().isoformat()))
    db.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
               ("total_messages", str(total)))
    
    db.commit()
    db.close()
    
    return {
        "total_files": total,
        "new_indexed": new,
        "channels": channels,
        "db_path": str(DB_PATH)
    }


def parity() -> dict:
    """Parity gate — compare backfill JSON count vs SQLite count (inspired by Atom #19)"""
    db = get_db()

    # Count JSON files in backfill directory
    json_count = 0
    for channel_dir in BACKFILL_ROOT.iterdir():
        if not channel_dir.is_dir() or channel_dir.name == "search.db":
            continue
        for date_dir in channel_dir.iterdir():
            if not date_dir.is_dir():
                continue
            daily_file = date_dir / "_daily.json"
            if daily_file.exists():
                msgs = json.loads(daily_file.read_text(encoding="utf-8"))
                json_count += len(msgs)

    # Count unique messages in SQLite (latest version only)
    db_count = db.execute(
        "SELECT COUNT(DISTINCT id) FROM messages WHERE is_tombstone = 0"
    ).fetchone()[0]

    db.close()

    missing = json_count - db_count
    return {
        "json_count": json_count,
        "db_count": db_count,
        "parity": missing == 0,
        "missing": missing,
        "extra": -missing if missing < 0 else 0
    }


def check() -> dict:
    """Check index health"""
    db = get_db()

    msg_count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    fts_count = db.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    meta = {row["key"]: row["value"] for row in db.execute("SELECT * FROM index_meta").fetchall()}

    # Version stats
    versioned = db.execute(
        "SELECT COUNT(*) as cnt FROM messages WHERE version > 1"
    ).fetchone()[0]

    tombstoned = db.execute(
        "SELECT COUNT(*) as cnt FROM messages WHERE is_tombstone = 1"
    ).fetchone()[0]

    db.close()

    parity_result = parity()

    return {
        "messages_in_db": msg_count,
        "fts_documents": fts_count,
        "in_sync": msg_count == fts_count,
        "parity": parity_result,
        "versioned_messages": versioned,
        "tombstoned_messages": tombstoned,
        "last_index": meta.get("last_index", "never"),
        "db_size": os.path.getsize(DB_PATH) if DB_PATH.exists() else 0
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: indexer.py <build|check|rebuild|parity>")
        print("  build    Index new messages (incremental, append-only)")
        print("  rebuild  Reindex everything from scratch")
        print("  check    Check index health + parity gate")
        print("  parity   Run parity gate: JSON count vs DB count")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "build":
        result = index_all(reindex=False)
        print(f"\n=== Index Build Complete ===")
        print(f"Files scanned: {result['total_files']}")
        print(f"New indexed: {result['new_indexed']}")
        print(f"DB path: {result['db_path']}")
        print(f"\nBy channel:")
        for ch, count in result['channels'].items():
            print(f"  {ch}: {count} msgs")

    elif cmd == "rebuild":
        result = index_all(reindex=True)
        print(f"\n=== Full Rebuild Complete ===")
        print(f"Total indexed: {result['new_indexed']}")
        print(f"DB path: {result['db_path']}")

    elif cmd == "parity":
        p = parity()
        print(f"\n=== Parity Gate ===")
        print(f"JSON (backfill dir): {p['json_count']}")
        print(f"SQLite (DB): {p['db_count']}")
        print(f"Parity: {'PASS' if p['parity'] else 'FAIL — missing ' + str(p['missing']) + ', extra ' + str(p['extra'])}")

    elif cmd == "check":
        status = check()
        print(f"\n=== Index Health ===")
        print(f"Messages in DB: {status['messages_in_db']}")
        print(f"FTS documents: {status['fts_documents']}")
        print(f"In sync: {'Yes' if status['in_sync'] else 'No - needs rebuild'}")
        print(f"Versioned (edited): {status['versioned_messages']}")
        print(f"Tombstoned (deleted): {status['tombstoned_messages']}")
        print(f"Parity gate: {'PASS' if status['parity']['parity'] else 'FAIL'}")
        if not status['parity']['parity']:
            print(f"  JSON: {status['parity']['json_count']}, DB: {status['parity']['db_count']}")
            print(f"  Missing: {status['parity']['missing']}, Extra: {status['parity']['extra']}")
        print(f"Last index: {status['last_index']}")
        print(f"DB size: {status['db_size']:,} bytes")

    else:
        print(f"Unknown command: {cmd}")
