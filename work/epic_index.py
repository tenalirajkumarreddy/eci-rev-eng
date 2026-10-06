import json, re, collections, os

OUT = r'C:\Users\rajku\Documents\eci rev eng\out'
idx = json.load(open(os.path.join(OUT, 'class_string_index.json'), encoding='utf-8'))
print('indexed classes:', len(idx))

RX = re.compile(r'(epic|elector|voter|roll|sir\b|nvsp|search)', re.I)

# Which app classes reference ElectorDetails / EpicDatum?
print()
print('=' * 74)
print('CLASSES REFERENCING ElectorDetails / EpicDatum / EpicRefrenceModel')
print('=' * 74)
KEYS = ['ElectorDetails', 'EpicDatum', 'EpicRefrenceModel', 'SearchByElectorDetails',
        'EpicSearchDeatils', 'Epicdetails', 'SearchLastElectorDetails']
refcount = collections.defaultdict(set)
for cls, strs in idx.items():
    blob = ' '.join(strs)
    for k in KEYS:
        if k.lower() in blob.lower():
            refcount[k].add(cls)
for k in KEYS:
    s = sorted(refcount[k])
    print(f'  {k}: {len(s)} classes')
    for c in s[:14]:
        print(f'      {c}')

# endpoint-ish strings mentioning epic/elector/voter
print()
print('=' * 74)
print('ENDPOINT-ISH STRINGS mentioning epic/elector/voter/roll/search')
print('=' * 74)
found = collections.defaultdict(set)
for cls, strs in idx.items():
    if not cls.startswith(('com/eci/', 'in/gov/eci/', 'in/nic/eci/', 'suvidha/', 'con/eci/', 'con/avcoding/')):
        continue
    for s in strs:
        if RX.search(s) and (s.startswith('/') or s.startswith('http') or '/' in s):
            found[s].add(cls)
for s in sorted(found):
    print(f'  {s}')
    for c in sorted(found[s])[:3]:
        print(f'        <- {c}')
