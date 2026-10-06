import os, re, json, collections, sys

BASE = r'C:\Users\rajku\Documents\eci rev eng'
SMALI = os.path.join(BASE, 'work', 'smali')
OUT = os.path.join(BASE, 'out')

CLASS_RE = re.compile(r'^\.class\s+(.*?)\s+(L[^;]+;)', re.M)
CLASS_RE2 = re.compile(r'^\.class\s+.*?(L[\w/$]+;)', re.M)
METH_RE = re.compile(r'^\.method\s+(.*)$', re.M)
CONST_RE = re.compile(r'const-string(?:/jumbo)?\s+(?:\w+,\s*)?"([^"]*)"')

# endpoint-ish / interesting string shapes
INTEREST = re.compile(r'''(
    ^https?://                                        # absolute URL
  | ^/[A-Za-z0-9_\-./{}]+$                            # absolute path
  | ^(?:v\d+|api|auth|user|users|login|logout|token|oauth)/  # api-ish path
  | \.(?:json|xml|php|asp|jsp)$                       # api file ext
  | ^(?:GET|POST|PUT|DELETE|PATCH)\s+/http            # method+url
  | ^(?:application|text)/                           # content types
  | ^[A-Z][A-Z0-9_]{3,}$                             # CONSTANT_CASE (config keys)
  | ^(?:[a-z]+\.){2,}[a-z]{2,}                        # bare domain
  | ^Bearer\s                                        # auth
  | ^[A-Za-z0-9+/]{40,}={0,2}$                       # long b64
)''', re.X)

SKIP_PKGS = (
    'Landroid/', 'Ljava/', 'Lkotlin/', 'Lkotlinx/', 'Ljavax/', 'Ldalvik/',
    'Landroidx/', 'Lcom/google/android/', 'Lcom/google/gms/',
    'Lcom/google/firebase/', 'Lcom/google/protobuf/', 'Lio/reactivex/',
    'Lokhttp3/', 'Lokio/', 'Lretrofit2/', 'Lcom/squareup/',
    'Lorg/apache/', 'Lorg/joda/', 'Lorg/xml/', 'Lorg/w3c/',
    'Lcom/itextpdf/', 'Lschemaorg_apache_xmlbeans/', 'Lcom/facebook/',
    'Lcom/bumptech/', 'Lcom/github/', 'Lio/fresco/', 'Lcom/anychart/',
    'Lcom/google/gson/', 'Lorg/json/', 'Lcom/badoo/', 'Lcom/bumptech/glide/',
    'Lcom/airbnb/', 'Lcom/yalantis/', 'Lcom/github/drjacky/',
    'Lcom/pairip/', 'Lorg/conscrypt/', 'Lorg/bouncycastle/',
    'Lcom/google/gms/', 'Landroidx/', 'Lcom/itextpdf/',
)

def is_lib(cls):
    return any(cls.startswith(p) for p in SKIP_PKGS)

stats = collections.Counter()
endpoints = {}          # string -> set of classes
api_ifaces = []         # retrofit-ish interfaces with annotations
const_index = {}        # class -> [strings]

files = []
for d in sorted(os.listdir(SMALI)):
    sub = os.path.join(SMALI, d)
    if not os.path.isdir(sub):
        continue
    for root, _, fns in os.walk(sub):
        for fn in fns:
            if fn.endswith('.smali'):
                files.append(os.path.join(root, fn))

stats['files'] = len(files)
ann_re = re.compile(r'^\.annotation\s+(?:runtime|system|build)\s+(Lretrofit2/http/[^;]+;|Ljavax/inject/[^;]+;)', re.M)
http_ann = re.compile(r'^\.annotation\s+\w+\s+Lretrofit2/http/(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|HTTP);', re.M)

for idx, fp in enumerate(files):
    try:
        txt = open(fp, 'r', encoding='utf-8', errors='replace').read()
    except Exception:
        continue
    m = CLASS_RE2.search(txt)
    cls = m.group(1) if m else '?'
    app_code = not is_lib(cls)
    if app_code:
        stats['app_classes'] += 1
    else:
        stats['lib_classes'] += 1

    if app_code:
        vals = CONST_RE.findall(txt)
        keep = [v for v in vals if INTEREST.search(v) and len(v) < 300]
        if keep:
            const_index[cls] = sorted(set(keep))[:400]
            for v in set(keep):
                endpoints.setdefault(v, set()).add(cls)
        # retrofit interfaces
        if 'Lretrofit2/http/' in txt and http_ann.search(txt):
            anns = http_ann.findall(txt)
            paths = [v for v in vals if v.startswith('/') or v.startswith('http')]
            api_ifaces.append((cls, sorted(set(anns)), sorted(set(paths))[:60]))
    if idx % 10000 == 0 and idx:
        print(f'  ...{idx}/{len(files)}', file=sys.stderr)

stats['endpoint_like_strings'] = len(endpoints)
stats['retrofit_ifaces'] = len(api_ifaces)

with open(os.path.join(OUT, 'class_string_index.json'), 'w', encoding='utf-8') as f:
    json.dump(const_index, f, indent=0)

with open(os.path.join(OUT, 'retrofit_interfaces.txt'), 'w', encoding='utf-8') as f:
    for cls, anns, paths in sorted(api_ifaces):
        f.write(f'== {cls}\n   methods: {",".join(anns)}\n')
        for p in paths:
            f.write(f'   path: {p}\n')
        f.write('\n')

with open(os.path.join(OUT, 'all_endpoint_strings.txt'), 'w', encoding='utf-8') as f:
    for v, clss in sorted(endpoints.items()):
        f.write(f'{v}\t{",".join(sorted(clss)[:4])}\n')

print()
print('=' * 70)
for k, v in stats.items():
    print(f'{k:32} {v}')
