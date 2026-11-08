#!/usr/bin/env python3
"""SC1 CLI check; selected by python3 tests/check_s1.py."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def call(db, *args):
    command = [sys.executable, str(ROOT / 'app/booking.py'), '--db', str(db), *args]
    result = subprocess.run(command, text=True, capture_output=True)
    print(json.dumps({'command':command,'returncode':result.returncode,
                      'stdout':result.stdout,'stderr':result.stderr},ensure_ascii=False),flush=True)
    if result.returncode != 0:
        raise AssertionError('CLI process failed')
    return json.loads(result.stdout)

def main():
    with tempfile.TemporaryDirectory(prefix='booking-sc1-') as folder:
        db = Path(folder) / 'bookings.sqlite'
        first = call(db,'create','--room','A','--start','2026-11-10T10:00:00','--end','2026-11-10T11:00:00','--title','原始周会')
        assert first['status'] == 201, first
        rejected = call(db,'create','--room','A','--start','2026-11-10T10:30:00','--end','2026-11-10T11:30:00','--title','临时讨论')
        assert rejected['status'] == 409, rejected
        print('SC1 response assertions passed',flush=True)

if __name__ == '__main__':
    main()
