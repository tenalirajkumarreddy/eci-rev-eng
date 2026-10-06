import os, re

SMALI = r'C:\Users\rajku\Documents\eci rev eng\work\smali'

def path_of(rel):
    base = os.path.basename(rel)
    for root, _, fns in os.walk(SMALI):
        for fn in fns:
            if fn == base:
                full = os.path.join(root, fn).replace('\\', '/')
                if full.endswith(rel):
                    return os.path.join(root, fn)
    return None

# --- TRestClient.getDetail contract ---
p = path_of('in/gov/eci/garuda/e2/repo/TRestClient.smali')
if p:
    txt = open(p, encoding='utf-8', errors='replace').read()
    blocks = re.split(r'(?=^\.method\s)', txt, flags=re.M)
    for b in blocks:
        m = re.match(r'^\.method\s+(.*)$', b, re.M)
        if not m or 'getDetail' not in m.group(1):
            continue
        sig = m.group(1).strip()
        names = re.search(r'names\s*=\s*\{([^}]*)\}', b)
        sigv = re.search(r'\.annotation system Ldalvik/annotation/Signature;.*?value\s*=\s*\{(.*?)\.end annotation', b, re.S)
        verb = re.search(r'\.annotation runtime Lretrofit2/http/(GET|POST|PUT|DELETE|PATCH);\s*\n\s*value\s*=\s*"([^"]*)"', b)
        hdrs = re.findall(r'Lretrofit2/http/Header;\s*\n\s*value\s*=\s*"([^"]*)"', b)
        body = 'Lretrofit2/http/Body;' in b
        qm = 'Lretrofit2/http/QueryMap;' in b
        print('=' * 76)
        print('TRestClient.getDetail')
        print('=' * 76)
        print('  signature :', sig[:200])
        if names:
            print('  paramName :', ' '.join(names.group(1).split()))
        print('  verb      :', verb.group(1) if verb else '?')
        print('  path      :', verb.group(2) if verb else '?')
        print('  headers   :', hdrs)
        print('  @Body     :', body, '   @QueryMap:', qm)
        if sigv:
            tv = ' '.join(x.strip().strip('",') for x in sigv.group(1).split('\n'))
            tv = re.sub(r'\s+', ' ', tv)
            print('  returns   :', tv[-220:])
        print()

# --- TApiClient base URLs ---
print('=' * 76)
print('TApiClient base URLs')
print('=' * 76)
p2 = path_of('in/gov/eci/garuda/e2/repo/TApiClient.smali')
if p2:
    t2 = open(p2, encoding='utf-8', errors='replace').read()
    for u in dict.fromkeys(re.findall(r'"(https?://[^"]+)"', t2)):
        if 'schemas.' in u or 'w3.org' in u:
            continue
        print('   ', u)
else:
    print('  TApiClient not found; searching garuda e2 repo dir')
    for root, _, fns in os.walk(SMALI):
        for fn in fns:
            if fn.endswith('.smali') and 'garuda/e2/repo' in os.path.join(root, fn).replace('\\', '/'):
                print('   ', fn)
