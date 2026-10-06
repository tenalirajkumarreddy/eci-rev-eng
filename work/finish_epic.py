import os, re, collections

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

# 1. find ANY definition of getOfficialDetailSecureKey across the citizen module
print('=' * 76)
print('getOfficialDetailSecureKey - definition search')
print('=' * 76)
found = []
for root, _, fns in os.walk(D13):
    for fn in fns:
        if not fn.endswith('.smali'):
            continue
        fp = os.path.join(root, fn)
        t = open(fp, encoding='utf-8', errors='replace').read()
        if 'getOfficialDetailSecureKey' in t and '.method' in t:
            for m in re.finditer(r'^\.method\s+[^\n]*getOfficialDetailSecureKey[^\n]*', t, re.M):
                rel = os.path.relpath(fp, SMALI).replace('\\', '/')
                cls = re.sub(r'^smali(_classes\d+)?/', '', rel)[:-6].replace('/', '.')
                found.append((cls, m.group(0).strip()))
                break
for cls, m in found:
    print(f'  {cls}')
    print(f'      {m}')
if not found:
    print('  NOT FOUND anywhere in citizen module (smali_classes13)')

# 2. dump it if found
def dump(cls_dotted, meth, maxl=90):
    rel = cls_dotted.replace('.', '/') + '.smali'
    p = path_of(rel)
    if not p:
        print(cls_dotted, 'NOT FOUND'); return
    lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith('.method') and meth in ln and 'bridge synthetic' not in ln:
            start = i; break
    if start is None:
        print(cls_dotted, meth, 'not found'); return
    print()
    print('=' * 76)
    print(f'{cls_dotted.split(".")[-1]}::{lines[start].strip()[:120]}')
    print('=' * 76)
    for j in range(start, min(len(lines), start + maxl)):
        s = lines[j].strip()
        if not s or s.startswith('.line') or s.startswith('.param') or s.startswith('.prologue'):
            continue
        print('  ' + s[:150])
        if s.startswith('.end method'):
            break

# dump whichever class defines it (prefer a base/parent class)
cands = [c for c, _ in found if 'BaseActivity' in c or 'AppController' in c or 'Application' in c] or [c for c, _ in found]
for c in cands[:1]:
    dump(c, 'getOfficialDetailSecureKey')

# 3. ElectroleSearchUpdate schema
print()
print('=' * 76)
print('ElectroleSearchUpdate schema (server response for api/search)')
print('=' * 76)
es = path_of('com/eci/citizen/DataRepository/ServerRequestEntity/electoralSearchEntity/ElectroleSearchUpdate.smali')
if es:
    t = open(es, encoding='utf-8', errors='replace').read()
    fields = re.findall(r'^\.field\s+(.*?)$', t, re.M)
    for f in fields[:80]:
        print('  ', f.strip())
    print('  total fields:', len(fields))
else:
    print('  not found')
