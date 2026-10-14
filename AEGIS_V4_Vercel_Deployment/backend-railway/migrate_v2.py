"""Copy V2 user data into V3 safely before first use.
Usage: python migrate_v2.py "C:/path/to/AEGIS_Pro_Helpdesk"
Existing target state is backed up before replacement. Never deletes source files.
"""
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEST=Path(__file__).resolve().parent

def migrate(source):
    source=Path(source).resolve()
    if not source.is_dir() or not (source/'data'/'inbox').is_dir():
        raise ValueError('Source must be an extracted AEGIS Pro folder containing data/inbox.')
    if source==DEST:
        raise ValueError('Source and destination cannot be the same folder.')
    backup=DEST/'migration_backups'/datetime.now().strftime('%Y%m%d_%H%M%S')
    backup.mkdir(parents=True,exist_ok=True)
    num=0
    for filename in ('outbox.json','agent_log.jsonl'):
        p=source/filename;target=DEST/filename
        if p.is_file():
            if target.is_file():shutil.copy2(target,backup/filename)
            shutil.copy2(p,target);num+=1
    srcdb=source/'state'/'aegis.db'
    dstdb=DEST/'state'/'aegis.db'
    if srcdb.exists():
        dstdb.parent.mkdir(parents=True,exist_ok=True)
        if dstdb.exists():
            with sqlite3.connect(str(dstdb)) as from_conn, sqlite3.connect(str(backup/'aegis.db')) as to_conn:from_conn.backup(to_conn)
        with sqlite3.connect(str(srcdb)) as from_conn, sqlite3.connect(str(dstdb)) as to_conn:from_conn.backup(to_conn)
        import storage
        storage.initialize()
        num+=1
    for p in (source/'data'/'inbox').glob('*.json'):
        target=DEST/'data'/'inbox'/p.name
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=p.read_bytes():
            shutil.copy2(target,backup/('conflicting_'+p.name))
        shutil.copy2(p,target);num+=1
    policy=source/'data'/'policy.md'
    if policy.exists():
        target=DEST/'data'/'policy.md'
        if target.exists():shutil.copy2(target,backup/'policy.md')
        shutil.copy2(policy,target);num+=1
    print(f'Migrated {num} artifacts from: {source}')
    print(f'Previous target copies (when applicable): {backup}')
    print('No Gmail tokens or Gemini secrets were migrated. Reconnect after launch.')

if __name__=='__main__':
    if len(sys.argv)<2:
        print('Usage: python migrate_v2.py "C:/path/to/old/AEGIS_Pro_Helpdesk"')
        raise SystemExit(2)
    try:migrate(sys.argv[1])
    except Exception as e:
        print('[ERROR]',e)
        raise SystemExit(1)
