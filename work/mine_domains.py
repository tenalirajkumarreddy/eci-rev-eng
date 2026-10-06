import re, collections, os

BASE = r'C:\Users\rajku\Documents\eci rev eng'
OUT = os.path.join(BASE, 'out')

lines = open(os.path.join(OUT, 'raw_strings.txt'), encoding='utf-8', errors='replace').read().split('\n')

TLD = ('com|net|org|io|co|in|ai|dev|app|cloud|xyz|info|biz|me|uk|ru|de|fr|jp|us|ca|au|'
       'edu|gov|mil|int|tech|online|site|live|store|pro|fm|gg|tv|so|sh|to|cc|eu|asia|'
       'world|space|solutions|systems|digital|network|services|group|team|agency|'
       'studio|design|media|health|finance|bank|capital|fund|exchange|market|trade|'
       'shop|email|news|blog|wiki|zone|link|click|ads|api|cdn|static|assets|files|'
       'data|db|sql|git|repo|ci|build|deploy|ops|infra|internal|intranet|corp|local|'
       'test|staging|prod|qa|uat|sandbox|demo|beta|alpha|preview|tools|util|utils|lib|'
       'libs|vendor|partner|partners|client|user|account|auth|login|oauth|identity|sso|'
       'pay|payment|payments|billing|invoice|order|cart|checkout|tracking|logistics|'
       'inventory|product|catalog|retail|supplier')

dom_re = re.compile(r'\b((?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+(?:' + TLD + r'))\b')

known = set()
for l in lines:
    for m in re.finditer(r'https?://([^/\s]+)', l):
        known.add(m.group(1).lower())

bare = collections.Counter()
ctx = collections.defaultdict(set)
for l in lines:
    if len(l) > 400:
        continue
    for m in dom_re.finditer(l):
        d = m.group(1).lower()
        if d in known or d.endswith(('.png', '.jpg', '.jpeg', '.gif', '.xml', '.json', '.css')):
            continue
        bare[d] += 1
        if len(ctx[d]) < 3:
            ctx[d].add(l.strip()[:160])

with open(os.path.join(OUT, 'bare_domains.txt'), 'w', encoding='utf-8') as f:
    for d, c in sorted(bare.items(), key=lambda x: -x[1]):
        f.write(f'{c}\t{d}\t|| ' + ' ~~ '.join(ctx[d]) + '\n')

print('bare (non-URL) domains:', len(bare))
print()
print('=== TOP 45 NON-URL DOMAINS ===')
for d, c in bare.most_common(45):
    print(f'{c:4d}  {d}')
