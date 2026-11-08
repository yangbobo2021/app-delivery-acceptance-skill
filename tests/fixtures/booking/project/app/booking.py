#!/usr/bin/env python3
"""Local meeting-room booking command line interface."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def emit(status, **data):
    print(json.dumps(dict(status=status, **data), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    for name in ("room", "start", "end", "title"):
        create.add_argument("--" + name, required=True)
    commands.add_parser("list")
    args = parser.parse_args()
    if args.command == "create":
        try:
            start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
            if start >= end or start.utcoffset() != end.utcoffset():
                raise ValueError("invalid interval")
        except ValueError:
            emit(400, error="start and end must be ordered ISO dates with matching timezone")
            return
        args.start, args.end = start.isoformat(), end.isoformat()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db) as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS bookings (id INTEGER PRIMARY KEY, room TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL)")
        if args.command == "list":
            emit(200, bookings=[dict(row) for row in db.execute("SELECT * FROM bookings ORDER BY id")])
            return
        existing = db.execute("SELECT * FROM bookings WHERE room = ? AND start < ? AND end > ? ORDER BY id LIMIT 1", (args.room, args.end, args.start)).fetchone()
        if existing:
            pass
            emit(409, error="room conflict", existing_id=existing["id"])
            return
        cursor = db.execute("INSERT INTO bookings (room,start,end,title,created_at) VALUES (?,?,?,?,?)", (args.room, args.start, args.end, args.title, datetime.now(timezone.utc).isoformat()))
        booking = db.execute("SELECT * FROM bookings WHERE id = ?", (cursor.lastrowid,)).fetchone()
        emit(201, booking=dict(booking))


if __name__ == "__main__":
    main()
