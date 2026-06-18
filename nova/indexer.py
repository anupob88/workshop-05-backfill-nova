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
        -- Messages table (source of truth)
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            channel_name TEXT,
            author TEXT,
            author_id TEXT,
            timestamp TEXT NOT NULL,
            content TEXT,
            has_attachments INTEGER DEFAULT 0,
            attachment_count INTEGER DEFAULT 0,
            raw_json TEXT
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
        db.execute("DELETE FROM messages")
        db.execute("DELETE FROM messages_fts")
        db.execute("DELETE FROM index_meta")
    
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
                    
                    # Check if already indexed
                    existing = db.execute(
                        "SELECT id FROM messages WHERE id = ?",
                        (msg["id"],)
                    ).fetchone()
                    
                    if existing:
                        continue
                    
                    new += 1
                    channels[channel_id] += 1
                    
                    db.execute("""
                        INSERT INTO messages (id, channel_id, channel_name, author, author_id, timestamp, content, has_attachments, attachment_count, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        msg["id"],
                        msg.get("channel_id", channel_id),
                        msg.get("channel_name", ""),
                        msg.get("author", ""),
                        msg.get("author_id", ""),
                        msg.get("timestamp", ""),
                        msg.get("content", ""),
                        1 if msg.get("attachments") else 0,
                        len(msg.get("attachments", [])),
                        json.dumps(msg, ensure_ascii=False)
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


def check() -> dict:
    """Check index health"""
    db = get_db()
    
    msg_count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    fts_count = db.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    meta = {row["key"]: row["value"] for row in db.execute("SELECT * FROM index_meta").fetchall()}
    
    db.close()
    
    return {
        "messages_in_db": msg_count,
        "fts_documents": fts_count,
        "in_sync": msg_count == fts_count,
        "last_index": meta.get("last_index", "never"),
        "db_size": os.path.getsize(DB_PATH) if DB_PATH.exists() else 0
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: indexer.py <build|check|rebuild>")
        print("  build    Index new messages (incremental)")
        print("  rebuild  Reindex everything from scratch")
        print("  check    Check index health")
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
    
    elif cmd == "check":
        status = check()
        print(f"\n=== Index Health ===")
        print(f"Messages in DB: {status['messages_in_db']}")
        print(f"FTS documents: {status['fts_documents']}")
        print(f"In sync: {'Yes' if status['in_sync'] else 'No - needs rebuild'}")
        print(f"Last index: {status['last_index']}")
        print(f"DB size: {status['db_size']:,} bytes")
    
    else:
        print(f"Unknown command: {cmd}")
