import re, collections, os, sys, json

BASE = r'C:\Users\rajku\Documents\eci rev eng'
SRC = os.path.join(BASE, 'out', 'raw_strings.txt')
OUT = os.path.join(BASE, 'out')

lines = open(SRC, encoding='utf-8', errors='replace').read().split('\n')

# --- URL extraction ---
url_re = re.compile(r'''https?://[^\s"'<>\)\]\},;]{4,300}''')
urls = collections.Counter()
for l in lines:
    for m in url_re.finditer(l):
        u = m.group().rstrip('.,;')
        urls[u] += 1

with open(os.path.join(OUT, 'urls.txt'), 'w', encoding='utf-8') as f:
    for u, c in sorted(urls.items()):
        f.write(f'{c}\t{u}\n')

hosts = collections.Counter()
for u in urls:
    m = re.match(r'https?://([^/]+)', u)
    if m:
        hosts[m.group(1).lower()] += 1

print('unique URLs:', len(urls))
print('unique hosts:', len(hosts))
print()
print('=== TOP 80 HOSTS (by URL count) ===')
for h, c in hosts.most_common(80):
    print(f'{c:5d}  {h}')

# --- bare hostnames / domains not in URLs ---
dom_re = re.compile(r'\b((?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+(?:com|net|org|io|co|in|ai|dev|app|cloud|xyz|info|biz|me|uk|ru|de|fr|jp|us|ca|au|edu|gov|mil|int|tech|online|site|live|store|pro|fm|gg|tv|so|sh|to|cc|eu|asia|world|space|solutions|systems|digital|network|services|group|team|agency|studio|design|media|health|finance|bank|insure|capital|fund|exchange|market|trade|shop|store|cloud|email|news|blog|wiki|zone|link|click|ads|app|dev|api|cdn|static|assets|media|files|data|db|sql|git|repo|ci|cd|build|deploy|ops|devops|infra|internal|intranet|corp|local|test|staging|prod|qa|uat|sandbox|demo|beta|alpha|preview|dev|tools|util|utils|lib|libs|vendor|third|partner|partners|client|clients|user|users|account|accounts|auth|login|signin|oauth|id|identity|sso|pay|payment|payments|billing|invoice|order|orders|cart|checkout|shipping|track|tracking|logistics|warehouse|inventory|product|catalog|store|shop|retail|wholesale|supplier|vendor|partner)\b')
known = set(hosts)
bare = collections.Counter()
for l in lines:
    if len(l) > 300:
        continue
    for m in dom_re.finditer(l):
        d = m.group(1).lower()
        if d in known:
            continue
        bare[d] += 1

with open(os.path.join(OUT, 'bare_domains.txt'), 'w', encoding='utf-8') as f:
    for d, c in sorted(bare.items(), key=lambda x: -x[1]):
        f.write(f'{c}\t{d}\n')
print()
print('bare (non-URL) domains found:', len(bare))
