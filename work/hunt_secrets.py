import re, collections, os, json

BASE = r'C:\Users\rajku\Documents\eci rev eng'
OUT = os.path.join(BASE, 'out')
SRC = os.path.join(OUT, 'raw_strings.txt')

lines = open(SRC, encoding='utf-8', errors='replace').read().split('\n')

# Each rule: (label, compiled regex, confidence)
RULES = [
 ('AWS Access Key ID',      re.compile(r'\b((?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ABIA|ACCA)[A-Z0-9]{16})\b'), 'HIGH'),
 ('AWS Secret Key',         re.compile(r'(?i)aws.{0,20}?(?:secret|sk).{0,20}?["\']([A-Za-z0-9/+=]{40})["\']'), 'HIGH'),
 ('Google API Key',         re.compile(r'\b(AIza[0-9A-Za-z\-_]{35})\b'), 'HIGH'),
 ('Google OAuth Client ID', re.compile(r'\b(\d{6,12}-[0-9a-z]{20,30}\.apps\.googleusercontent\.com)\b'), 'HIGH'),
 ('Google Cloud / FCM Key', re.compile(r'\b(AAAA[A-Za-z0-9_\-]{7}:[A-Za-z0-9_\-]{140,200})\b'), 'HIGH'),
 ('Firebase Realtime DB',   re.compile(r'(https://[a-z0-9\-]+\.firebaseio\.com)'), 'MED'),
 ('Firebase Project ID',    re.compile(r'\b(project_id["\']?\s*[:=]\s*["\']?)([a-z0-9\-]{6,30})'), 'MED'),
 ('GCP Service Account',    re.compile(r'"?[a-z0-9\-]+@[a-z0-9\-]+\.iam\.gserviceaccount\.com"?'), 'HIGH'),
 ('GCP Private Key',        re.compile(r'-----BEGIN (?:RSA |EC |)?PRIVATE KEY-----'), 'CRIT'),
 ('JWT',                    re.compile(r'\b(eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})\b'), 'HIGH'),
 ('Slack Token',            re.compile(r'\b(xox[baprs]-[A-Za-z0-9\-]{10,})'), 'HIGH'),
 ('Slack Webhook',          re.compile(r'(https://hooks\.slack\.com/services/T[A-Za-z0-9/]{20,})'), 'HIGH'),
 ('Stripe Live Key',        re.compile(r'\b((?:sk|rk)_live_[A-Za-z0-9]{20,})\b'), 'CRIT'),
 ('Stripe Test Key',        re.compile(r'\b((?:sk|rk)_test_[A-Za-z0-9]{20,})\b'), 'LOW'),
 ('Razorpay Key',           re.compile(r'\b(razorpay_[a-z_]+)\b'), 'MED'),
 ('Paytm Token',            re.compile(r'\b(APP_PAYTM_[A-Z_]+)\b'), 'LOW'),
 ('Twilio SID',             re.compile(r'\b(AC[0-9a-f]{32})\b'), 'HIGH'),
 ('Twilio Key',             re.compile(r'(?i)twilio.{0,30}?["\']([0-9a-f]{32})["\']'), 'HIGH'),
 ('SendGrid Key',           re.compile(r'\b(SG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,})\b'), 'HIGH'),
 ('Mailgun Key',            re.compile(r'\b(key-[0-9a-z]{32})\b'), 'HIGH'),
 ('Basic Auth in URL',      re.compile(r'https?://[^/\s:@]+:([^/\s@]{3,})@[^\s/]+'), 'HIGH'),
 ('Bearer literal',         re.compile(r'(?i)["\']?(bearer)\s+([A-Za-z0-9_\-\.=]{20,})'), 'MED'),
 ('Hardcoded password-ish', re.compile(r'(?i)\b(?:pass(?:word|wd)|pwd|secret|api[_-]?key|apikey|access[_-]?key|auth[_-]?token|client[_-]?secret|private[_-]?key)\b\s*[:=]\s*["\']([^"\'\s]{6,80})["\']'), 'HIGH'),
 ('Generic Base64 blob >40', re.compile(r'(?<![\w+/=])[A-Za-z0-9+/]{48,}={0,2}(?![\w+/=])'), 'LOW'),
 ('Hex key >=32',           re.compile(r'(?<![\w])[a-fA-F0-9]{40,}(?![\w])'), 'LOW'),
 ('API-key-ish assignment', re.compile(r'\b([A-Z][A-Z0-9_]{4,40}(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL))\b'), 'MED'),
 ('IV/AES constant 16b',    re.compile(r'(?i)(?:aes|iv|cbc|ecb|gcm)[^\n]{0,30}?["\']([0-9a-fA-F]{16,32})["\']'), 'HIGH'),
 ('RSA public exponent/key',re.compile(r'(?i)["\'](M(?:IG|II|A|E|AQ|IG|IGb)[A-Za-z0-9+/=]{40,})["\']'), 'MED'),
 ('TrustManager / AllTrust',re.compile(r'trustr?allcert|AllowAllHostnameVerifier|X509TrustManager'), 'HIGH'),
 ('SQL keyword in code',    re.compile(r'(?i)\b(select\s+.+?\s+from|insert\s+into|update\s+\w+\s+set)\b'), 'LOW'),
]

findings = collections.defaultdict(list)
for ln in lines:
    s = ln.strip()
    if not s or len(s) > 2000:
        continue
    for label, rx, conf in RULES:
        for m in rx.finditer(s):
            val = m.group(1) if m.groups() else m.group(0)
            if not val:
                continue
            key = (label, val[:200])
            if len(findings[key]) < 2:
                findings[key].append(s[:220])

# Deduplicate, filter obvious noise
NOISE = re.compile(r'^(?:[A-Z_]{40,}|[a-z_]{1,20})$')
rows = []
for (label, val), ctx in findings.items():
    rows.append((label, val, ctx))

# Filter generic-base64 noise that is just proguard/java descriptors
def is_noise(label, val):
    if label in ('Generic Base64 blob >40', 'Hex key >=32'):
        # likely obfuscated identifiers / resource names if no digits+letters mix
        if not re.search(r'[0-9]', val):
            return True
        if len(set(val)) <= 6:
            return True
    if label == 'API-key-ish assignment':
        if val in ('API_KEY_USED', 'EXCEPTION_MESSAGE', 'PASSWORD', 'TOKEN'):
            return True
    return False

rows = [r for r in rows if not is_noise(r[0], r[1])]

by_label = collections.defaultdict(list)
for label, val, ctx in rows:
    by_label[label].append((val, ctx))

print('=' * 74)
print('SECRET / CREDENTIAL HUNT')
print('=' * 74)
total = 0
for label, items in sorted(by_label.items(), key=lambda x: -len(x[1])):
    uniq = {}
    for val, ctx in items:
        uniq.setdefault(val, ctx)
    print()
    print(f'### {label}  ({len(uniq)} unique)')
    for val, ctx in list(uniq.items())[:25]:
        print(f'   VALUE : {val}')
        if ctx:
            print(f'   CONTEXT: {ctx[0][:200]}')
    total += len(uniq)

print()
print('=' * 74)
print(f'TOTAL unique secret-candidate values: {total}')
print('=' * 74)

with open(os.path.join(OUT, 'secrets.txt'), 'w', encoding='utf-8') as f:
    for label, items in sorted(by_label.items()):
        uniq = {}
        for val, ctx in items:
            uniq.setdefault(val, ctx)
        for val, ctx in uniq.items():
            f.write(f'[{label}] {val}\n')
            if ctx:
                f.write(f'    ctx: {ctx[0][:250]}\n')
