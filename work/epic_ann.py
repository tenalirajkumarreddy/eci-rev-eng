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

RC = path_of('com/eci/citizen/DataRepository/RestClient.smali')
txt = open(RC, encoding='utf-8', errors='replace').read()
blocks = re.split(r'(?=^\.method\s)', txt, flags=re.M)

WANT = ['getEpicElectorDetail', 'getElectorsDetailsApi', 'searchElectoral',
        'searchElectoralOne', 'searchElectoralElectroleDetail',
        'searchElectoralPdf', 'verifyElectoral', 'getNvspUserDetails']

for b in blocks:
    m = re.match(r'^\.method\s+(.*)$', b, re.M)
    if not m:
        continue
    sig = m.group(1).strip()
    if not any(w in sig for w in WANT):
        continue
    print('=' * 78)
    print('METHOD:', sig)
    print('=' * 78)
    # print everything up to and including '.end method' but strip bytecode noise
    lines = b.split('\n')
    for ln in lines:
        s = ln.strip()
        if s.startswith('.method'):
            print('  ' + s)
        elif s.startswith('.annotation') or s.startswith('.locals') or s.startswith('.param'):
            continue
        elif s.startswith('value') or s.startswith('encoded') or s.startswith('true') or s.startswith('false'):
            print('       ' + s)
        elif re.match(r'^[\w\[\]/.$"-]+$', s) and ('retrofit2/http' in s or 'Ljava/lang/String' in s or 'Ljava/util/Map' in s or 'Lretrofit2/Call' in s):
            print('       ' + s)
        elif s.startswith('.end method'):
            print('  .end method')
            break
    print()
