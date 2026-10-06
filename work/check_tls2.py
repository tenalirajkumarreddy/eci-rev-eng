import os, re

SMALI = r'C:\Users\rajku\Documents\eci rev eng\work\smali'
OUT = r'C:\Users\rajku\Documents\eci rev eng\out'

found = {}
for root, _, fns in os.walk(SMALI):
    for fn in fns:
        if fn.endswith('.smali'):
            if fn in ('ApiClient.smali', 'ApiClient$1.smali', 'ApiClientForms.smali', 'ApiClientForms$1.smali'):
                found[fn] = os.path.join(root, fn)

def dump(fn, pats, ctx=8):
    p = found.get(fn)
    if not p:
        print(f'{fn}: NOT FOUND'); return
    lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
    print('=' * 74)
    print(fn, f'({len(lines)} lines)')
    print('=' * 74)
    shown = set()
    for i, ln in enumerate(lines):
        if any(re.search(x, ln) for x in pats):
            for j in range(max(0, i-ctx), min(len(lines), i+ctx+1)):
                if j not in shown:
                    shown.add(j)
                    print(f'  {j+1:5d}| {lines[j][:145]}')
            print('  ' + '-'*70)
    print()

dump('ApiClient.smali', [
    r'ApiClient\$1', r'TLSSocketFactory', r'CustomSSLSocketFactory',
    r'HostnameVerifier', r'ALLOW_ALL', r'SSLContext', r'sslSocketFactory',
    r'X509TrustManager', r'new-instance.*ApiClient',
])
dump('ApiClient$1.smali', [
    r'\.class', r'\.super', r'checkServerTrusted', r'checkClientTrusted',
    r'getAcceptedIssuers', r'return-void', r'const/4',
])

# Which files reference ApiClient$1 (the trust-all TM)?
print('=' * 74)
print('FILES REFERENCING THE TRUST-ALL TRUSTMANAGER (ApiClient$1)')
print('=' * 74)
refs = []
for root, _, fns in os.walk(SMALI):
    for fn in fns:
        if not fn.endswith('.smali'):
            continue
        fp = os.path.join(root, fn)
        try:
            txt = open(fp, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        if 'Lcom/eci/citizen/DataRepository/ApiClient$1;' in txt:
            refs.append(os.path.relpath(fp, SMALI))
for r in sorted(refs)[:40]:
    print('  ', r)
print('total referencing files:', len(refs))
