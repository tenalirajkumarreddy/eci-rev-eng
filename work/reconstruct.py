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

def dump(cls, meth, maxl=200):
    p = path_of(cls + '.smali')
    if not p:
        print(cls, 'NOT FOUND'); return
    lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith('.method') and re.search(r'\b' + re.escape(meth) + r'\b', ln) and 'bridge synthetic' not in ln:
            start = i; break
    if start is None:
        print(f'{cls}::{meth} not found'); return
    print('=' * 76)
    print(f'{cls.split("/")[-1]}::{lines[start].strip()}')
    print('=' * 76)
    for j in range(start, min(len(lines), start + maxl)):
        s = lines[j].strip()
        if not s or s.startswith('.line'):
            continue
        if s.startswith('.param') or s.startswith('.prologue') or s.startswith('.locals'):
            continue
        print('  ' + s[:155])
        if s.startswith('.end method'):
            break
    print()

# the real (non-bridge) callSearchApi
dump('com/eci/citizen/features/home/evp/DigitalEpicActivity', 'callSearchApi')
# EPIC input validation
dump('com/eci/citizen/features/eepic/EdigitalEpic', 'isValidation')
