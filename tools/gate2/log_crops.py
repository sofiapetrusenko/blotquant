#!/usr/bin/env python3
"""Append crop provenance rows: crop sha256/px + parent file sha256."""
import csv, hashlib, sys
from pathlib import Path
from PIL import Image

crops = sorted(Path('crops').glob('*.png'))
parents = {p.name.lower(): p for p in Path('images').glob('*.jpg')}
out = Path('crops/crop_log.csv')
new = not out.exists()
with out.open('a', newline='') as f:
    w = csv.writer(f)
    if new: w.writerow(['crop','crop_sha256','px','parent','parent_sha256','panel_note'])
    logged = set()
    if not new:
        logged = {r[0] for r in csv.reader(out.open()) if r}
    for c in crops:
        if c.name in logged: continue
        stem = c.stem.split('__')[0].lower() + '.jpg'
        parent = parents.get(stem)
        if not parent:
            print(f'!! {c.name}: no parent match for {stem}'); continue
        blob = c.read_bytes()
        with Image.open(c) as im: px = f'{im.width}x{im.height}'
        note = c.stem.split('__')[1] if '__' in c.stem else ''
        w.writerow([c.name, hashlib.sha256(blob).hexdigest(), px,
                    parent.name, hashlib.sha256(parent.read_bytes()).hexdigest(), note])
        print(f'{c.name}  {px}')
print('log -> crops/crop_log.csv')
