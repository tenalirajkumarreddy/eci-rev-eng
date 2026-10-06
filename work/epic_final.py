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

def dump_method(cls, methname, maxlines=170):
    p = path_of(cls + '.smali')
    if not p:
        print(cls, 'NOT FOUND'); return
    lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith('.method') and methname in ln:
            start = i; break
    if start is None:
        print(f'{cls}: method {methname} not found'); return
    print('=' * 78)
    print(f'{cls}  ::  {lines[start].strip()}')
    print('=' * 78)
    for j in range(start, min(len(lines), start + maxlines)):
        s = lines[j].strip()
        if not s:
            continue
        if s.startswith('.line'):
            continue
        print('  ' + s[:155])
        if s.startswith('.end method'):
            break
    print()

dump_method('com/eci/citizen/features/home/evp/DigitalEpicActivity', 'callSearchApi')
dump_method('com/eci/citizen/features/eepic/EdigitalEpic', 'callEpicSearchDetailTrial')
