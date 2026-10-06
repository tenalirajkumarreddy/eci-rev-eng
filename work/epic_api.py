import os, re

SMALI = r'C:\Users\rajku\Documents\eci rev eng\work\smali'
D13 = os.path.join(SMALI, 'smali_classes13')

def path_of(rel_suffix):
    base = os.path.basename(rel_suffix)
    for root, _, fns in os.walk(D13):
        for fn in fns:
            if fn == base:
                full = os.path.join(root, fn).replace('\\', '/')
                if full.endswith(rel_suffix):
                    return os.path.join(root, fn)
    return None

RC = path_of('com/eci/citizen/DataRepository/RestClient.smali')
txt = open(RC, encoding='utf-8', errors='replace').read()

# split into methods, keeping annotations
print('=' * 78)
print('RestClient: methods matching epic / elector / voter / search / roll / sir')
print('=' * 78)
blocks = re.split(r'(?=^\.method\s)', txt, flags=re.M)
RX = re.compile(r'(epic|elector|voter|search|roll|sir|nvsp)', re.I)
count = 0
for b in blocks:
    m = re.match(r'^\.method\s+(.*)$', b, re.M)
    if not m:
        continue
    sig = m.group(1).strip()
    if not RX.search(sig):
        continue
    count += 1
    http_ann = re.findall(r'^\.annotation\s+\w+\s+Lretrofit2/http/([A-Z]+);', b, re.M)
    path_ann = re.findall(r'^\s*value\s*=\s*"(.*?)"\s*$', b, re.M)
    qs = re.findall(r'^\.annotation\s+runtime\s+Lretrofit2/http/Query;\s*\n\s*value\s*=\s*"(.*?)"', b, re.M)
    headers = re.findall(r'^\.annotation\s+runtime\s+Lretrofit2/http/Headers;\s*\n(?:\s*value\s*=\s*\{\s*)?(.*)$', b, re.M)
    print()
    print(f'  METHOD : {sig[:150]}')
    if http_ann:
        print(f'  HTTP   : {http_ann}')
    pv = [p for p in path_ann if p.startswith('/') or p.startswith('http')]
    if pv:
        print(f'  PATH   : {pv}')
    if qs:
        print(f'  QUERY  : {qs}')
print()
print('matched methods:', count)

# ---- base URLs ----
print()
print('=' * 78)
print('BASE URL CANDIDATES (const-strings that look like base URLs)')
print('=' * 78)
for cls in ['com/eci/citizen/DataRepository/ApiClientModule.smali',
            'com/eci/citizen/DataRepository/ApiClient.smali',
            'com/eci/citizen/DataRepository/RestClient.smali']:
    p = path_of(cls)
    if not p:
        print(f'  {cls}: NOT FOUND'); continue
    t = open(p, encoding='utf-8', errors='replace').read()
    urls = [u for u in re.findall(r'"(https?://[^"]+)"', t) if 'schemas.' not in u and 'w3.org' not in u]
    print()
    print(f'  --- {cls} ---')
    for u in dict.fromkeys(urls):
        print('     ', u)
