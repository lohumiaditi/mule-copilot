"""MIS case log in SQLite: the centralised record SOP Annex III para 7 asks for.

    cases       one row per case: stage, hold level and the clock's deadlines
    events      every step with its case date and actor (system, officer, customer, LEA)
    documents   every drafted document: note, customer notices, police reference, STR

Case dates are the simulated dates the workflow runs on; logged_at is wall-clock time.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime

from mule_copilot import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY, account_id TEXT, opened_on TEXT, stage TEXT, hold_level TEXT,
    hold_date TEXT, explanation_due TEXT, decision_due TEXT, reference_date TEXT, release_on TEXT,
    closed_on TEXT, outcome TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, case_id TEXT, case_date TEXT, logged_at TEXT, step TEXT, actor TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY, case_id TEXT, kind TEXT, case_date TEXT, content TEXT, verifier_errors TEXT);
"""
CASE_FIELDS = {"account_id", "opened_on", "stage", "hold_level", "hold_date", "explanation_due", "decision_due",
               "reference_date", "release_on", "closed_on", "outcome"}


_ready = set()   # databases whose tables this process has already created


def _connect():
    if config.MIS_DB not in _ready:
        config.MIS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.MIS_DB)
    conn.row_factory = sqlite3.Row
    if config.MIS_DB not in _ready:
        conn.executescript(SCHEMA)
        _ready.add(config.MIS_DB)
    return conn


def _now():
    return datetime.now().isoformat(timespec="seconds")


def update_case(case_id, **fields):
    fields = {k: (str(v) if v is not None else None) for k, v in fields.items() if k in CASE_FIELDS}
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO cases (case_id, updated_at) VALUES (?, ?)", (case_id, _now()))
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE cases SET {sets}, updated_at = ? WHERE case_id = ?", [*fields.values(), _now(), case_id])


def log(case_id, case_date, step, actor, detail=""):
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO events (case_id, case_date, logged_at, step, actor, detail) VALUES (?, ?, ?, ?, ?, ?)",
                     (case_id, str(case_date), _now(), step, actor, detail))


def save_document(case_id, kind, case_date, content, errors=()):
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO documents (case_id, kind, case_date, content, verifier_errors) VALUES (?, ?, ?, ?, ?)",
                     (case_id, kind, str(case_date), content, json.dumps(list(errors))))


def _rows(query, *args):
    with closing(_connect()) as conn:
        return [dict(r) for r in conn.execute(query, args)]


def cases():
    return _rows("SELECT * FROM cases ORDER BY opened_on, case_id")


def case(case_id):
    rows = _rows("SELECT * FROM cases WHERE case_id = ?", case_id)
    return rows[0] if rows else None


def events(case_id):
    return _rows("SELECT * FROM events WHERE case_id = ? ORDER BY id", case_id)


def documents(case_id):
    return _rows("SELECT * FROM documents WHERE case_id = ? ORDER BY id", case_id)


def reset(case_id):
    with closing(_connect()) as conn, conn:
        for table in ("cases", "events", "documents"):
            conn.execute(f"DELETE FROM {table} WHERE case_id = ?", (case_id,))
