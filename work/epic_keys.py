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

# ---------- 1. HTTP verbs for the epic methods ----------
RC = path_of('com/eci/citizen/DataRepository/RestClient.smali')
txt = open(RC, encoding='utf-8', errors='replace').read()
blocks = re.split(r'(?=^\.method\s)', txt, flags=re.M)
WANT = ['getEpicElectorDetail', 'getElectorsDetailsApi', 'searchElectoral(',
        'searchElectoralOne', 'searchElectoralElectroleDetail', 'searchElectoralPdf',
        'verifyElectoral', 'getNvspUserDetails']
print('=' * 78)
print('EPIC-SEARCH API CONTRACTS (verb + path + headers)')
print('=' * 78)
for b in blocks:
    m = re.match(r'^\.method\s+(.*)$', b, re.M)
    if not m:
        continue
    sig = m.group(1).strip()
    if not any(w in sig for w in WANT):
        continue
    verb = re.search(r'\.annotation\s+\w+\s+(Lretrofit2/http/(?:GET|POST|PUT|DELETE|PATCH);)', b)
    pathm = re.findall(r'\.annotation\s+\w+\s+Lretrofit2/http/\w+;\s*\n\s*value\s*=\s*"([^"]*)"', b)
    qs = re.findall(r'\.annotation\s+runtime\s+Lretrofit2/http/Query;\s*\n(?:\s*\w+\s*=\s*\w+\s*\n)?\s*value\s*=\s*"([^"]*)"', b)
    hd = re.findall(r'\.annotation\s+runtime\s+Lretrofit2/http/Headers;\s*\n((?:\s*\w+\s*=\s*\{?\s*"[^"]*"\s*\}?,?\s*\n)+)', b)
    print()
    short = re.sub(r'\(.*', '', sig)
    print(f'  {short}')
    print(f'      verb  : {verb.group(1).replace("Lretrofit2/http/","").rstrip(";") if verb else "?"}')
    print(f'      path  : {pathm}')
    if qs:
        print(f'      query : {qs}')
    if hd:
        print(f'      header: {hd}')

# ---------- 2. X-API-KEY literal values across app code ----------
print()
print('=' * 78)
print('X-API-KEY / Authorization HEADER VALUES (app-owned classes)')
print('=' * 78)
KEYRX = re.compile(r'const-string(?:/jumbo)?\s+(?:\w+,\s*)"([^"]{4,120})"')
vals = {}
for root, _, fns in os.walk(D13):
    for fn in fns:
        if not fn.endswith('.smali'):
            continue
        fp = os.path.join(root, fn)
        full = fp.replace('\\', '/')
        if '/com/eci/citizen/' not in full and '/in/gov/eci/' not in full:
            continue
        t = open(fp, encoding='utf-8', errors='replace').read()
        if 'X-API-KEY' not in t and 'AuthorizedVHA' not in t and 'passKey' not in t:
            continue
        rel = os.path.relpath(fp, SMALI).replace('\\', '/')
        cls = re.sub(r'^smali(_classes\d+)?/', '', rel)[:-6].replace('/', '.')
        for v in KEYRX.findall(t):
            if re.search(r'(?i)authorization|apikey|AuthorizedVHA|passKey|Bearer|^[A-Za-z0-9_\-]{12,}$', v):
                vals.setdefault(v, set()).add(cls)
for v in sorted(vals):
    print(f'  {v!r}')
    for c in sorted(vals[v])[:3]:
        print(f'       <- {c}')

# ---------- 3. Retrofit base URL -> interface wiring in ApiClient ----------
print()
print('=' * 78)
print('ApiClient: Retrofit .baseUrl(...) wiring')
print('=' * 78)
AC = path_of('com/eci/citizen/DataRepository/ApiClient.smali')
t = open(AC, encoding='utf-8', errors='replace').read()
lines = t.split('\n')
# find const-string url followed shortly by baseUrl call
for i, ln in enumerate(lines):
    m = re.search(r'const-string(?:/jumbo)?\s+\w+,\s*"(https?://[^"]+)"', ln)
    if not m:
        continue
    url = m.group(1)
    if 'schemas.' in url or 'w3.org' in url:
        continue
    window = '\n'.join(lines[i:i+40])
    iface = re.findall(r'Lretrofit2/Retrofit;->baseUrl\(Ljava/lang/String;\)', window)
    create = 'create(' in window
    which = re.findall(r'Lcom/eci/citizen/DataRepository/\w+;', window)
    print()
    print(f'  baseUrl: {url}')
    if iface:
        print(f'     -> .baseUrl() called   (Retrofit instance built)')
    for w in dict.fromkeys(which):
        print(f'        nearby: {w}')
