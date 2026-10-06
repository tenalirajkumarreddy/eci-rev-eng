import os, re, json, collections

BASE = r'C:\Users\rajku\Documents\eci rev eng'
SMALI = os.path.join(BASE, 'work', 'smali')
OUT = os.path.join(BASE, 'out')

TARGETS = [
    'DksTCGeT0LE4hfgN5asDFYCtl3IDmvWb',
    'ca3dd8d70fb3cda8c45c2a5d51471b50',
    'AIzaSyDV7pay2SoqbNKVQgY-sHen9-E2BIUNzMM',
]
PATS = {t: re.compile(re.escape(t)) for t in TARGETS}

CLASS_RE = re.compile(r'^\.class\s+.*?(L[\w/$]+;)', re.M)

hits = collections.defaultdict(list)
files = []
for d in sorted(os.listdir(SMALI)):
    sub = os.path.join(SMALI, d)
    if os.path.isdir(sub):
        for root, _, fns in os.walk(sub):
            for fn in fns:
                if fn.endswith('.smali'):
                    files.append(os.path.join(root, fn))

print('scanning', len(files), 'smali files for hardcoded token context...')
for fp in files:
    try:
        txt = open(fp, 'r', encoding='utf-8', errors='replace').read()
    except Exception:
        continue
    for t, rx in PATS.items():
        if rx.search(txt):
            m = CLASS_RE.search(txt)
            cls = m.group(1) if m else os.path.basename(fp)
            # capture the enclosing .method for each hit
            lines = txt.split('\n')
            meth = None
            ctxs = []
            for i, ln in enumerate(lines):
                if ln.startswith('.method'):
                    meth = ln.strip()[:110]
                for tt, rrx in PATS.items():
                    if rrx.search(ln):
                        ctxs.append((tt, meth, ln.strip()[:150]))
            hits[t].append((cls, os.path.relpath(fp, SMALI), ctxs))

print()
print('=' * 74)
print('HARDCODED SECRET -> LOCATION MAP')
print('=' * 74)
for t in TARGETS:
    print()
    print(f'### {t}')
    if not hits[t]:
        print('   (no match)')
        continue
    for cls, rel, ctxs in hits[t][:6]:
        print(f'  CLASS : {cls}')
        print(f'  FILE  : {rel}')
        for tt, meth, ln in ctxs[:4]:
            print(f'     [{meth}]')
            print(f'        {ln}')
        print()
