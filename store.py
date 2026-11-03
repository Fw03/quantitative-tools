"""Local experiment records and replayable data snapshots; never sent externally."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
from uuid import uuid4
from market import now


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def code_hash():
    digest = hashlib.sha256()
    for name in ('app.py', 'engine.py', 'analytics.py', 'strategies.py', 'research.py', 'market.py'):
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


class RunStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(__file__).parent / 'data' / 'experiments.sqlite3'

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                connection.execute('''CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
                    source TEXT NOT NULL, result TEXT NOT NULL, snapshot TEXT NOT NULL)''')
                yield connection
        finally:
            connection.close()

    def save(self, kind, result, data, options, source, strategy_keys=None, replay_of=None):
        identifier = uuid4().hex
        stamp = now()
        snapshot = dict(data=data, options=options, strategy_keys=strategy_keys,
                        data_sha256=hashlib.sha256(canonical(data).encode()).hexdigest(),
                        code_sha256=code_hash(), replay_of=replay_of)
        result = dict(result, run_id=identifier, created_at=stamp,
                      provenance={k:v for k,v in snapshot.items() if k not in ('data',)},
                      source=source)
        with self.connect() as connection:
            connection.execute('INSERT INTO experiments VALUES (?,?,?,?,?,?)',
                               (identifier, stamp, kind, source, canonical(result), canonical(snapshot)))
            connection.execute('DELETE FROM experiments WHERE id NOT IN (SELECT id FROM experiments ORDER BY created_at DESC, rowid DESC LIMIT 50)')
        return result

    def recent(self):
        with self.connect() as connection:
            rows=connection.execute('SELECT id,created_at,kind,source FROM experiments ORDER BY created_at DESC,rowid DESC LIMIT 50').fetchall()
        return [dict(zip(('id','created_at','kind','source'),row)) for row in rows]

    def get(self, identifier, include_data=False):
        if not re.fullmatch(r'[0-9a-f]{32}', identifier):
            return None
        with self.connect() as connection:
            row=connection.execute('SELECT kind,result,snapshot FROM experiments WHERE id=?',(identifier,)).fetchone()
        if row is None:
            return None
        value=dict(kind=row[0], result=json.loads(row[1]))
        if include_data:
            value['snapshot']=json.loads(row[2])
        return value
