#!/usr/bin/env python3
"""SC2 existing unrelated check. Preserve this file."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[1]
def call(db,*args):
    result = subprocess.run([sys.executable,str(ROOT/'app/booking.py'),'--db',str(db),*args],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)
with tempfile.TemporaryDirectory(prefix='booking-sc2-') as folder:
    db=Path(folder)/'db.sqlite'
    made=[]
    for room in ('A','B'):
        r=call(db,'create','--room',room,'--start','2026-11-10T10:00:00','--end','2026-11-10T11:00:00','--title','跨会议室')
        assert r['status']==201,r
        made.append(r['booking'])
    listed=call(db,'list')
    assert listed['status']==200,listed
    assert listed['bookings']==made,listed
print('SC2 passed')
