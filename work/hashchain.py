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

def dump(cls, meth, maxl=120):
    p = path_of(cls + '.smali')
    if not p:
        print(f'--- {cls} NOT FOUND ---'); return
    lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith('.method') and re.search(r'\b' + re.escape(meth) + r'\b', ln) and 'bridge synthetic' not in ln:
            start = i; break
    if start is None:
        print(f'--- {cls}::{meth} not found ---'); return
    print('=' * 76)
    print(f'{cls.split("/")[-1]}::{lines[start].strip()[:130]}')
    print('=' * 76)
    for j in range(start, min(len(lines), start + maxl)):
        s = lines[j].strip()
        if not s or s.startswith('.line') or s.startswith('.param') or s.startswith('.prologue'):
            continue
        print('  ' + s[:150])
        if s.startswith('.end method'):
            break
    print()

dump('com/eci/citizen/features/home/evp/DigitalEpicActivity', 'getOfficialDetailSecureKey')
dump('com/eci/citizen/utility/Utils', 'GetHashNew')

# also: any other GetHash* / secure key helpers
print('=' * 76)
print('Utils: hashing / crypto helpers present')
print('=' * 76)
p = path_of('com/eci/citizen/utility/Utils.smali')
if p:
    for m in re.findall(r'^\.method\s+(.*)$', open(p, encoding='utf-8', errors='replace').read(), re.M):
        if re.search(r'(hash|md5|sha|encrypt|decrypt|cipher|key|secure|aes|base64)', m, re.I):
            print('  ', m.strip()[:130])
