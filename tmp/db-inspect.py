#!/usr/bin/env python
"""Inspect social_observations.db — what's registered, what's collected, what's stale."""
import sqlite3, json
from datetime import datetime, timezone, timedelta

DB = r"D:\vncen\saas\bounty-api-fresh\data\social_observations.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== TABLES ===")
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
for t in tables:
    count = c.execute(f"SELECT COUNT(*) FROM [{t[0]}]").fetchone()[0]
    print(f"  {t[0]}: {count} rows")

print("\n=== REGISTERED QUERIES ===")
try:
    queries = c.execute("SELECT * FROM scheduled_queries ORDER BY id").fetchall()
    for q in queries:
        d = dict(q)
        print(f"\n  Query: {d.get('query','?')} | platform={d.get('platform','?')} | scope={d.get('scope','?')}")
        print(f"    id={d.get('id','?')} | interval={d.get('collection_interval_minutes','?')}min")
        print(f"    last_run={d.get('last_collected_at','?')}")
        print(f"    next_due={d.get('next_due_at','?')}")
        print(f"    enabled={d.get('enabled','?')}")
except Exception as e:
    print(f"  Error: {e}")

print("\n=== RECENT COLLECTION RUNS (last 10) ===")
try:
    runs = c.execute("""
        SELECT * FROM collection_runs 
        ORDER BY started_at DESC LIMIT 10
    """).fetchall()
    for r in runs:
        d = dict(r)
        print(f"\n  Run: {d.get('id','?')[:8]}... | query_id={d.get('query_id','?')}")
        print(f"    started={d.get('started_at','?')} | ended={d.get('ended_at','?')}")
        print(f"    status={d.get('status','?')} | items={d.get('item_count','?')}")
        print(f"    routes_tried={d.get('routes_tried','?')[:200] if d.get('routes_tried') else '?'}")
except Exception as e:
    print(f"  Error: {e}")

print("\n=== RECENT OBSERVATIONS (last 15) ===")
try:
    obs = c.execute("""
        SELECT post_id, platform, subreddit, score, num_comments, title, observed_at
        FROM observations 
        ORDER BY observed_at DESC LIMIT 15
    """).fetchall()
    for o in obs:
        d = dict(o)
        title = (d.get('title') or '')[:70]
        print(f"  [{d.get('observed_at','?')}] r/{d.get('subreddit','?')} | score={d.get('score','?')} | {title}")
except Exception as e:
    print(f"  Error: {e}")

print("\n=== SOURCE RECORDS (last 5) ===")
try:
    recs = c.execute("""
        SELECT post_id, platform, source_type, observed_at
        FROM source_records 
        ORDER BY observed_at DESC LIMIT 5
    """).fetchall()
    for r in recs:
        d = dict(r)
        print(f"  [{d.get('observed_at','?')}] {d.get('post_id','?')} | {d.get('platform','?')} | {d.get('source_type','?')}")
except Exception as e:
    print(f"  Error: {e}")

print("\n=== UNIQUE POSTS COLLECTED ===")
try:
    total = c.execute("SELECT COUNT(DISTINCT post_id) FROM observations").fetchone()[0]
    by_sub = c.execute("""
        SELECT subreddit, COUNT(DISTINCT post_id) as posts, 
               MIN(observed_at) as first, MAX(observed_at) as last
        FROM observations 
        WHERE subreddit IS NOT NULL
        GROUP BY subreddit ORDER BY posts DESC
    """).fetchall()
    print(f"  Total unique posts: {total}")
    for s in by_sub:
        d = dict(s)
        print(f"  r/{d['subreddit']}: {d['posts']} posts | {d['first']} → {d['last']}")
except Exception as e:
    print(f"  Error: {e}")

conn.close()
