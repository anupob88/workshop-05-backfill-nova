#!/usr/bin/env python3
"""
Nova Backfill Query — Search and Analytics over Discord Backfill Data
Queries SQLite FTS5 index built by indexer.py

Part of Workshop 05: Backfill Midterm
Nova Oracle — 2026-06-19
"""

import json, os, sys, sqlite3
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "agents" / "data" / "psi" / "backfill" / "search.db"


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def search(
    query: str,
    channel: str = None,
    author: str = None,
    limit: int = 10,
    after: str = None,
    before: str = None
) -> list:
    """Full-text search across backfill index"""
    db = get_db()
    
    where = []
    params = []
    
    if channel:
        where.append("m.channel_id LIKE ?")
        params.append(f"%{channel}%")
    
    if author:
        where.append("m.author LIKE ?")
        params.append(f"%{author}%")
    
    if after:
        where.append("m.timestamp >= ?")
        params.append(after)
    
    if before:
        where.append("m.timestamp <= ?")
        params.append(before)
    
    where_clause = " AND ".join(where) if where else "1=1"
    
    if query:
        sql = f"""
            SELECT m.id, m.channel_name, m.author, m.timestamp, 
                   snippet(messages_fts, 2, '<b>', '</b>', '...', 32) as snippet,
                   m.content, m.has_attachments
            FROM messages_fts
            JOIN messages m ON messages_fts.rowid = m.rowid
            WHERE messages_fts MATCH ? AND {where_clause}
            ORDER BY rank
            LIMIT ?
        """
        rows = db.execute(sql, (query, *params, limit)).fetchall()
    else:
        sql = f"""
            SELECT m.id, m.channel_name, m.author, m.timestamp, m.content, m.has_attachments
            FROM messages m
            WHERE {where_clause}
            ORDER BY m.timestamp DESC
            LIMIT ?
        """
        rows = db.execute(sql, (*params, limit)).fetchall()
    
    results = []
    for row in rows:
        r = dict(row)
        if "content" in r and len(r["content"]) > 300:
            r["content"] = r["content"][:300] + "..."
        results.append(r)
    
    db.close()
    return results


def stats(channel: str = None) -> dict:
    """Get backfill statistics"""
    db = get_db()
    
    channel_filter = "WHERE channel_id LIKE ?" if channel else ""
    params = (f"%{channel}%",) if channel else ()
    
    total = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    
    by_channel = db.execute("""
        SELECT channel_id, channel_name, COUNT(*) as cnt,
               MIN(timestamp) as first, MAX(timestamp) as last
        FROM messages GROUP BY channel_id ORDER BY cnt DESC
    """).fetchall()
    
    by_author = db.execute("""
        SELECT author, author_id, COUNT(*) as cnt
        FROM messages GROUP BY author_id ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    
    by_date = db.execute("""
        SELECT DATE(timestamp) as day, COUNT(*) as cnt
        FROM messages
        GROUP BY day ORDER BY day DESC LIMIT 14
    """).fetchall()
    
    attachments = db.execute(
        "SELECT COUNT(*) FROM messages WHERE has_attachments = 1"
    ).fetchone()[0]
    
    db.close()
    
    return {
        "total_messages": total,
        "attachments": attachments,
        "by_channel": [dict(r) for r in by_channel],
        "top_authors": [dict(r) for r in by_author],
        "by_date": [{"date": r["day"], "count": r["cnt"]} for r in by_date],
        "db_size": os.path.getsize(DB_PATH) if DB_PATH.exists() else 0
    }


def export(channel: str = None, format: str = "markdown") -> str:
    """Export messages to markdown or JSON"""
    db = get_db()
    
    if channel:
        rows = db.execute(
            "SELECT * FROM messages WHERE channel_id LIKE ? ORDER BY timestamp",
            (f"%{channel}%",)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM messages ORDER BY timestamp"
        ).fetchall()
    
    if format == "json":
        return json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False, default=str)
    
    # Markdown format
    lines = ["# Discord Backfill Export", f"Exported: {datetime.now().isoformat()}", f"Total: {len(rows)} messages", ""]
    current_date = None
    
    for row in rows:
        r = dict(row)
        date = r["timestamp"][:10] if r["timestamp"] else "unknown"
        
        if date != current_date:
            current_date = date
            lines.append(f"\n## {date}\n")
        
        lines.append(f"**{r['author']}** ({r['timestamp']}):")
        lines.append(r["content"] or "[no content]")
        if r["has_attachments"]:
            lines.append(f"  📎 *has attachments*")
        lines.append("")
    
    db.close()
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: query.py <search|stats|export> [options]")
        print()
        print("  search <query> [--channel=ID] [--author=NAME] [--limit=N]")
        print("      Full-text search across indexed messages")
        print()
        print("  stats [--channel=ID]")
        print("      Show backfill statistics and analytics")
        print()
        print("  export [--channel=ID] [--format=json|markdown]")
        print("      Export messages to JSON or Markdown")
        sys.exit(1)
    
    cmd = sys.argv[1]
    kwargs = {}
    for arg in sys.argv[2:]:
        if arg.startswith("--"):
            key, _, val = arg[2:].partition("=")
            kwargs[key] = val
        elif cmd == "search":
            kwargs["query"] = arg
    
    if cmd == "search":
        query = kwargs.pop("query", "")
        if not query:
            print("Usage: query.py search <query> [options]")
            sys.exit(1)
        
        limit = int(kwargs.pop("limit", 10))
        results = search(query, limit=limit, **kwargs)
        
        print(f"\n=== Search: '{query}' ({len(results)} results) ===\n")
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r['channel_name']}] {r['author']} — {r['timestamp']}")
            if "snippet" in r:
                print(f"   {r['snippet']}")
            else:
                print(f"   {r.get('content', '[no content]')[:150]}")
            print()
    
    elif cmd == "stats":
        s = stats(**kwargs)
        print(f"\n=== Nova Backfill Stats ===")
        print(f"Total messages: {s['total_messages']:,}")
        print(f"With attachments: {s['attachments']}")
        print(f"DB size: {s['db_size']:,} bytes")
        print(f"\nBy channel:")
        for ch in s["by_channel"]:
            print(f"  {ch['channel_name'] or ch['channel_id']}: {ch['cnt']} msgs")
        print(f"\nTop authors:")
        for a in s["top_authors"]:
            print(f"  {a['author']}: {a['cnt']} msgs")
        print(f"\nRecent activity:")
        for d in s["by_date"]:
            bar = "█" * min(d["count"], 50)
            print(f"  {d['date']}: {bar} ({d['count']})")
    
    elif cmd == "export":
        fmt = kwargs.pop("format", "markdown")
        output = export(format=fmt, **kwargs)
        
        out_file = ROOT / "agents" / "data" / "psi" / "backfill" / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt if fmt != 'markdown' else 'md'}"
        out_file.write_text(output, encoding="utf-8")
        print(f"Exported to: {out_file}")
        print(f"Size: {len(output):,} chars")
    
    else:
        print(f"Unknown command: {cmd}")
