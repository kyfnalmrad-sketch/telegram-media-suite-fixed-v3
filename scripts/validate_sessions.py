from pathlib import Path
import sqlite3

for path in sorted(Path('.tmd-data').glob('**/*.session')):
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            row = db.execute('SELECT dc_id, api_id, test_mode, auth_key, user_id, is_bot FROM sessions LIMIT 1').fetchone() if 'sessions' in tables else None
            print({
                'path': str(path),
                'size': path.stat().st_size,
                'integrity': integrity,
                'tables': tables,
                'session_row': bool(row),
                'auth_key_bytes': len(row[3]) if row else 0,
                'user_id_present': bool(row and row[4]),
                'is_bot': bool(row and row[5]),
            })
    except Exception as exc:
        print({'path': str(path), 'error': type(exc).__name__})
