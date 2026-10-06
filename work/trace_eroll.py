import os, re, glob

SMALI = r'C:\Users\rajku\Documents\eci rev eng\work\smali'
OUT = r'C:\Users\rajku\Documents\eci rev eng\out'

def find(sub):
    res = []
    want = sub.replace('.', '/') + '.smali'
    base = sub.rsplit('/', 1)[-1] + '.smali'
    for root, _, fns in os.walk(SMALI):
        for fn in fns:
            if fn != base:
                continue
            full = os.path.join(root, fn).replace('\\', '/')
            if full.endswith(want):
                res.append(os.path.join(root, fn))
    return res

def consts(path):
    txt = open(path, encoding='utf-8', errors='replace').read()
    return txt, re.findall(r'const-string(?:/jumbo)?\s+(?:\w+,\s*)"([^"]*)"', txt)

def methods(path):
    txt = open(path, encoding='utf-8', errors='replace').read()
    return re.findall(r'^\.method\s+(.*)$', txt, re.M)

def invocations(path, pat=None):
    txt = open(path, encoding='utf-8', errors='replace').read()
    out = re.findall(r'^(\s*invoke-\S+\s+\{[^}]*\},\s*(L[\w/$]+;)->([^ ]+))', txt, re.M)
    res = []
    for full, cls, meth in out:
        if pat is None or re.search(pat, cls + '->' + meth, re.I):
            res.append((cls, meth))
    return res

TARGETS = [
    ('com/eci/citizen/features/eRoll/SearchbyElectorSir', 'EPIC SEARCH UI'),
    ('com/eci/citizen/features/eRoll/SearchByLastSir', 'LAST-NAME SEARCH UI'),
    ('com/eci/citizen/features/eRoll/SearchLastElectorDetails', 'DETAILS SCREEN'),
]

for path_, label in TARGETS:
    for fp in find(path_):
        print('=' * 76)
        print(f'{label}:  {path_}')
        print('=' * 76)
        txt, cs = consts(fp)
        interesting = [c for c in cs if len(c) > 2]
        print('-- const-strings --')
        for c in dict.fromkeys(interesting):
            print('   ', repr(c))
        print()
        print('-- outbound calls (app-owned / network / crypto) --')
        seen = set()
        for cls, meth in invocations(fp):
            if not re.search(r'Lcom/eci/|Lin/gov/eci/|Lin/nic/eci/|Lokhttp3/|Lretrofit2/|Ljavax/crypto|', cls):
                continue
            k = (cls, meth)
            if k in seen:
                continue
            seen.add(k)
            print(f'    {cls}->{meth}')
        print()
