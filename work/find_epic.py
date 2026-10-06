import os, re, json, collections

BASE = r'C:\Users\rajku\Documents\eci rev eng'
SMALI = os.path.join(BASE, 'work', 'smali')
OUT = os.path.join(BASE, 'out')

# 1) find classes whose NAME hints at epic / voter search
NAME_RX = re.compile(r'(epic|votersearch|searchby|searchvoter|namerecord|voterlist|electoralroll|eroll|voterdetail|findvoter|searchepic)', re.I)

hits = collections.defaultdict(set)
files = []
for d in sorted(os.listdir(SMALI)):
    sub = os.path.join(SMALI, d)
    if os.path.isdir(sub):
        for root, _, fns in os.walk(sub):
            for fn in fns:
                if fn.endswith('.smali'):
                    files.append(os.path.join(root, fn))

print('scanning', len(files), 'files for epic/voter-search classes...')
for fp in files:
    b = os.path.basename(fp)
    if NAME_RX.search(b):
        rel = os.path.relpath(fp, SMALI)
        # normalize: strip dex dir + .smali
        cls = rel.replace('\\', '/').split('/')[-1][:-6]
        cls = re.sub(r'^smali(_classes\d+)?/', '', rel)[:-6].replace('/', '.')
        hits['classes'].add(cls)

print()
print('=' * 74)
print('CLASSES MATCHING epic/voter-search NAMES')
print('=' * 74)
for c in sorted(hits['classes']):
    if c.startswith(('android.', 'com.google.', 'androidx.')):
        continue
    print('  ', c)
