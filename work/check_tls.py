import os, re

SMALI = r'C:\Users\rajku\Documents\eci rev eng\work\smali'

targets = [
 'smali_classes2/com/eci/citizen/DataRepository/ApiClient.smali',
 'smali_classes2/com/eci/citizen/DataRepository/ApiClient$1.smali',
 'smali_classes2/com/eci/citizen/DataRepository/sslclasses/CustomSSLSocketFactory.smali',
 'smali_classes2/com/eci/citizen/DataRepository/sslclasses/TLSSocketFactory.smali',
]

# locate them wherever they live
found = {}
for root, _, fns in os.walk(SMALI):
    for fn in fns:
        if fn.startswith(('ApiClient', 'CustomSSLSocketFactory', 'TLSSocketFactory')) and fn.endswith('.smali'):
            found[fn] = os.path.join(root, fn)

print('located:')
for k in sorted(found):
    print('  ', k)

def show(fn, patterns, ctx=6):
    p = found.get(fn)
    if not p:
        print(f'\n--- {fn}: NOT FOUND ---')
        return
    txt = open(p, encoding='utf-8', errors='replace').read()
    lines = txt.split('\n')
    print(f'\n{"="*72}\n{fn}   ({len(lines)} lines)\n{"="*72}')
    cls = re.search(r'^\.class\s+(.*)$', txt, re.M)
    sup = re.search(r'^\.super\s+(.*)$', txt, re.M)
    if cls: print(cls.group(0))
    if sup: print(sup.group(0))
    print()
    hits = set()
    for i, ln in enumerate(lines):
        for pat in patterns:
            if re.search(pat, ln):
                hits.add(i)
    for i in sorted(hits):
        lo, hi = max(0, i - 3), min(len(lines), i + 4)
        print(f'  [line {i+1}]')
        for j in range(lo, hi):
            mark = '>>' if j == i else '  '
            print(f'  {mark} {lines[j][:150]}')
        print()

show('ApiClient$1.smali', [r'checkServerTrusted', r'checkClientTrusted', r'getAcceptedIssuers',
                           r'TLSSocketFactory', r'CustomSSLSocketFactory', r'HostnameVerifier',
                           r'ALLOW_ALL', r'trustAll'])
show('CustomSSLSocketFactory.smali', [r'checkServerTrusted', r'checkClientTrusted',
                                      r'getAcceptedIssuers', r'TLSSocketFactory', r'X509TrustManager',
                                      r'throw new'])
show('TLSSocketFactory.smali', [r'checkServerTrusted', r'checkClientTrusted',
                                r'getAcceptedIssuers', r'TLSSocketFactory', r'X509TrustManager',
                                r'throw new', r'createSocket'])
show('ApiClient.smali', [r'TLSSocketFactory', r'CustomSSLSocketFactory', r'HostnameVerifier',
                         r'ALLOW_ALL', r'trustAll', r'CertificatePinner', r'SSLContext'])
