import os, re

SMALI = r'C:\Users\rajku\Documents\eci rev eng\work\smali'
D13 = os.path.join(SMALI, 'smali_classes13')

def path_of(cls_dotted):
    rel = cls_dotted.replace('.', '/') + '.smali'
    for root, _, fns in os.walk(D13):
        for fn in fns:
            if fn == os.path.basename(rel):
                full = os.path.join(root, fn).replace('\\', '/')
                if full.endswith(rel):
                    return os.path.join(root, fn)
    return None

def report(cls_dotted, show_invoke=True, filt=None):
    fp = path_of(cls_dotted)
    print('=' * 78)
    print(cls_dotted)
    print('=' * 78)
    if not fp:
        print('  NOT FOUND'); return
    txt = open(fp, encoding='utf-8', errors='replace').read()
    lines = txt.split('\n')
    print(f'  lines: {len(lines)}   file: {os.path.relpath(fp, SMALI)}')
    print()
    print('  -- declared methods --')
    for m in re.findall(r'^\.method\s+(.*)$', txt, re.M):
        print('     ', m.strip()[:120])
    print()
    print('  -- const-strings --')
    for c in dict.fromkeys(re.findall(r'const-string(?:/jumbo)?\s+(?:\w+,\s*)"([^"]*)"', txt)):
        print('     ', repr(c))
    if show_invoke:
        print()
        print('  -- calls into app/network/crypto --')
        seen = set()
        for cls, meth in re.findall(r'^\s*invoke-\S+\s+\{[^}]*\},\s*(L[\w/$]+;)->([^ ]+)', txt, re.M):
            if not re.search(r'Lcom/eci/|Lin/gov/eci/|Lin/nic/eci/|Lokhttp3/|Lretrofit2/|Ljavax/crypto|Lorg/json/', cls):
                continue
            if filt and not re.search(filt, cls + '->' + meth):
                continue
            k = (cls, meth)
            if k in seen:
                continue
            seen.add(k)
            print(f'     {cls}->{meth}')
    print()

report('com.eci.citizen.features.eepic.EdigitalEpic')
report('com.eci.citizen.DataRepository.RestClient')
