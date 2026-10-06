import os, re, collections, sys

SMALI = r'C:\Users\rajku\Documents\eci rev eng\work\smali'

# locate dex dirs containing the citizen module
needles = ['ElectorDetails', 'EpicDatum', 'EpicRefrenceModel',
           'SearchByElectorDetails', 'EpicSearchDeatils', 'Epicdetails',
           'EpicSearch', 'SearchEpic']
roots = []
for d in sorted(os.listdir(SMALI)):
    sub = os.path.join(SMALI, d)
    if not os.path.isdir(sub):
        continue
    hit = False
    for root, _, fns in os.walk(sub):
        if 'com/eci/citizen' in root.replace('\\', '/'):
            hit = True
            break
    if hit:
        roots.append(sub)
print('dex dirs holding com/eci/citizen:', [os.path.basename(r) for r in roots])

results = collections.defaultdict(set)   # needle -> set of class names
files_scanned = 0
CLASS_RE = re.compile(r'^\.class\s+.*?(L[\w/$]+;)', re.M)

for sub in roots:
    for root, _, fns in os.walk(sub):
        for fn in fns:
            if not fn.endswith('.smali'):
                continue
            fp = os.path.join(root, fn)
            try:
                txt = open(fp, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            files_scanned += 1
            for n in needles:
                if n in txt:
                    m = CLASS_RE.search(txt)
                    rel = os.path.relpath(fp, SMALI).replace('\\', '/')
                    cls = re.sub(r'^smali(_classes\d+)?/', '', rel)[:-6].replace('/', '.')
                    results[n].add(cls)

print('files scanned:', files_scanned)
print()
print('=' * 74)
print('CROSS-REFERENCES (class names only)')
print('=' * 74)
for n in needles:
    s = sorted(results[n])
    print()
    print(f'### {n}   ({len(s)} classes)')
    for c in s[:40]:
        print('   ', c)
