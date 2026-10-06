import os, re

SMALI = r'C:\Users\rajku\Documents\eci rev eng\work\smali'
D13 = os.path.join(SMALI, 'smali_classes13')

def path_of(rel):
    base = os.path.basename(rel)
    for root, _, fns in os.walk(D13):
        for fn in fns:
            if fn == base:
                full = os.path.join(root, fn).replace('\\', '/')
                if full.endswith(rel):
                    return os.path.join(root, fn)
    return None

# ---- 1. call sites of the eepic / search methods, with surrounding const-strings ----
CALLERS = ['getEpicElectorDetail', 'searchElectoralPdf', 'searchElectoralOne',
           'searchElectoral(', 'verifyElectoral', 'getElectorsDetailsApi']
CLASSES = ['com/eci/citizen/features/eepic/EdigitalEpic',
           'com/eci/citizen/features/electoralSearch/ElectoralSearchActivity',
           'com/eci/citizen/features/home/evp/DigitalEpicActivity']

for cls in CLASSES:
    p = path_of(cls + '.smali')
    if not p:
        print(f'{cls}: NOT FOUND'); continue
    lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
    hits = []
    for i, ln in enumerate(lines):
        if any(c in ln for c in CALLERS) and 'invoke-interface' in ln:
            hits.append(i)
    if not hits:
        continue
    print('=' * 78)
    print(cls)
    print('=' * 78)
    for i in hits:
        # find enclosing method
        meth = None
        for j in range(i, -1, -1):
            if lines[j].startswith('.method'):
                meth = lines[j].strip()[:120]; break
        lo, hi = max(0, i - 45), min(len(lines), i + 6)
        print(f'\n--- call at line {i+1}  in  {meth} ---')
        for j in range(lo, hi):
            s = lines[j].strip()
            if not s:
                continue
            mark = '>>' if j == i else '  '
            print(f'  {mark} {s[:150]}')
    print()

# ---- 2. base URL paired with the eepic-capable Retrofit ----
print('=' * 78)
print('Searching for eepic-related base URL + X-API-KEY value literals')
print('=' * 78)
for root, _, fns in os.walk(D13):
    for fn in fns:
        if not fn.endswith('.smali'):
            continue
        fp = os.path.join(root, fn)
        full = fp.replace('\\', '/')
        if not any(x in full for x in ('/com/eci/citizen/', '/in/gov/eci/')):
            continue
        t = open(fp, encoding='utf-8', errors='replace').read()
        if 'electoralsearch' not in t.lower():
            continue
        rel = os.path.relpath(fp, SMALI).replace('\\', '/')
        clsn = re.sub(r'^smali(_classes\d+)?/', '', rel)[:-6].replace('/', '.')
        urls = [u for u in re.findall(r'"(https?://[^"]*electoral[^"]*)"', t)]
        urls += [u for u in re.findall(r'"(https?://[^"]*eepic[^"]*)"', t)]
        urls += [u for u in re.findall(r'"(https?://[^"]*voters?\.eci[^"]*)"', t)]
        if urls:
            print(f'  {clsn}')
            for u in dict.fromkeys(urls):
                print(f'      {u}')
