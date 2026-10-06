import re, collections, os

BASE = r'C:\Users\rajku\Documents\eci rev eng'
OUT = os.path.join(BASE, 'out')
lines = open(os.path.join(OUT, 'raw_strings.txt'), encoding='utf-8', errors='replace').read().split('\n')

RULES = [
 ('Google API Key',        re.compile(r'\b(AIza[0-9A-Za-z\-_]{35})\b')),
 ('Google OAuth Client ID',re.compile(r'\b(\d{6,12}-[0-9a-z]{20,30}\.apps\.googleusercontent\.com)\b')),
 ('FCM Legacy Server Key', re.compile(r'\b(AAAA[A-Za-z0-9_\-]{7}:APA91b[A-Za-z0-9_\-]{60,})\b')),
 ('FCM Sender ID',         re.compile(r'\b(\d{10,14})\b')),
 ('AWS Access Key',        re.compile(r'\b((?:AKIA|ASIA|AROA|AIDA)[A-Z0-9]{16})\b')),
 ('GCP Service Account',   re.compile(r'\b([a-z0-9\-]{5,30}@[a-z0-9\-]{5,30}\.iam\.gserviceaccount\.com)\b')),
 ('PEM Private Key',       re.compile(r'-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----')),
 ('JWT',                   re.compile(r'\b(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})\b')),
 ('Slack Token',           re.compile(r'\b(xox[baprs]-[A-Za-z0-9\-]{10,})')),
 ('Slack Webhook',         re.compile(r'(https://hooks\.slack\.com/services/T[A-Za-z0-9/]{20,})')),
 ('Stripe Key',            re.compile(r'\b((?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,})\b')),
 ('Razorpay',              re.compile(r'\b(razorpay_[a-z_]+)\b')),
 ('Twilio SID',            re.compile(r'\b(AC[0-9a-f]{32})\b')),
 ('SendGrid',              re.compile(r'\b(SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,})\b')),
 ('Mailgun',               re.compile(r'\b(key-[0-9a-z]{32})\b')),
 ('Basic Auth in URL',     re.compile(r'https?://[^/\s:@"]+:([^/\s"@]{3,40})@[^\s/"]{3,60}')),
 ('Auth header literal',   re.compile(r'(?i)["\']?\b(?:authorization|x-api-key|apikey)\b["\']?\s*[:=]\s*["\']([^"\']{8,120})["\']')),
 ('Assigned secret',       re.compile(r'(?i)\b\w*(?:password|passwd|pwd|secret|api_?key|apikey|access_?key|auth_?token|client_?secret|private_?key|encryption_?key)\w*\b\s*[:=]\s*["\']([^"\'\s]{5,90})["\']')),
 ('AES/IV constant',       re.compile(r'(?i)["\']([0-9a-fA-F]{16}|[0-9a-fA-F]{24}|[0-9a-fA-F]{32})["\']\s*(?:,\s*["\'0-9a-fA-F]{8,}["\']\s*)?[,)]?\s*(?://|;)?\s*(?:aes|iv|key|secret|cipher|des)')),
 ('Crypto algo enum',      re.compile(r'(?i)\b(AES(?:/\w+/\w+)?|DESede|Blowfish|RC4|PBKDF2(?:WithHmacSHA\w+)?|RSA/ECB/(?:OAEPWithSHA-?256AndMGF1Padding|PKCS1Padding)|AES/GCM/NoPadding)\b')),
 ('Hostname verifier lax', re.compile(r'(?i)(ALLOW_ALL_HOSTNAME_VERIFIER|AllowAllHostnameVerifier|trustAllCerts|trustManager|TRUST_ALL|disableSSLVerification|setHostnameVerifier\(\*\)|InsecureTrustManager)')),
 ('Cleartext HTTP (non-local)', re.compile(r'\bhttp://(?!localhost|127\.0\.0\.1|schemas\.|\bwww\.w3\.org|ns\.adobe|xml\.org|purl\.org|schemas\.)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(/[^\s"\']*)?')),
 ('Firebase RTDB URL',     re.compile(r'(https://[a-z0-9\-]+\.firebaseio\.com)')),
 ('Firebase project id',   re.compile(r'\b([a-z0-9][a-z0-9\-]{4,28})\.apps\.pot\.google\.com\b')),
]

NOISE_EXACT = {
 'apikey', 'application', 'android', 'authKey', 'secret', 'password',
}
res = collections.defaultdict(dict)
for ln in lines:
    s = ln.strip()
    if not s or len(s) > 3000:
        continue
    for label, rx in RULES:
        for m in rx.finditer(s):
            gs = m.groups()
            g = gs[0] if gs else None
            val = (g if g else m.group(0)).strip()
            if not val or len(val) < 6:
                continue
            if val.lower() in NOISE_EXACT:
                continue
            d = res[label]
            if val not in d:
                d[val] = s[:240]

print('=' * 76)
print('HIGH-SIGNAL SECRET / CONFIG FINDINGS')
print('=' * 76)
total = 0
for label in [r[0] for r in RULES]:
    d = res.get(label, {})
    if not d:
        continue
    print()
    print(f'### {label}  ({len(d)} unique)')
    for val, ctx in list(d.items())[:40]:
        print(f'  VAL : {val}')
        print(f'  CTX : {ctx}')
        print()
    total += len(d)

print('=' * 76)
print(f'TOTAL: {total}')
print('=' * 76)

with open(os.path.join(OUT, 'secrets_highsignal.txt'), 'w', encoding='utf-8') as f:
    for label in [r[0] for r in RULES]:
        for val, ctx in res.get(label, {}).items():
            f.write(f'[{label}]\n  VAL: {val}\n  CTX: {ctx}\n')
