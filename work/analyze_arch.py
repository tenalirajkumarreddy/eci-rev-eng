import os, re, json, collections

BASE = r'C:\Users\rajku\Documents\eci rev eng'
SMALI = os.path.join(BASE, 'work', 'smali')
UNP = os.path.join(BASE, 'work', 'unpacked')
OUT = os.path.join(BASE, 'out')

CLASS_RE = re.compile(r'^\.class\s+.*?(L[\w/$]+;)', re.M)

# ---- 1. top-level package map (app-owned namespaces) ----
SKIP = ('Landroid/','Ljava/','Lkotlin/','Lkotlinx/','Ljavax/','Ldalvik/','Landroidx/',
        'Lcom/google/','Lio/reactivex/','Lokhttp3/','Lokio/','Lretrofit2/','Lorg/apache/',
        'Lorg/joda/','Lorg/xml/','Lorg/w3c/','Lcom/itextpdf/','Lschemaorg_apache_xmlbeans/',
        'Lcom/facebook/','Lcom/bumptech/','Lio/fresco/','Lcom/anychart/','Lorg/json/',
        'Lcom/badoo/','Lcom/github/','Lcom/pairip/','Lorg/conscrypt/','Lorg/bouncycastle/',
        'Lcom/squareup/','Lio/fresco/','Lcom/yalantis/','Lcom/airbnb/','Lcom/google/gson/')
def owned(cls):
    return not any(cls.startswith(s) for s in SKIP)

pkg_count = collections.Counter()
pkg_klasses = collections.defaultdict(list)

# ---- 2. capability markers ----
MARKERS = {
    'WebView usage':            re.compile(r'Landroid/webkit/WebView;|Landroid/webkit/WebViewClient;|setJavaScriptEnabled|addJavascriptInterface'),
    'JS bridge (@JavascriptInterface)': re.compile(r'Landroid/webkit/JavascriptInterface;'),
    'SSL/TLS custom':           re.compile(r'Ljavax/net/ssl/(SSLContext|X509TrustManager|HostnameVerifier|TrustManager);|Lokhttp3/CertificatePinner;'),
    'Trust-all / bypass':       re.compile(r'trustAllCerts|AllowAllHostnameVerifier|ALLOW_ALL_HOSTNAME_VERIFIER|checkServerTrusted|checkClientTrusted|setHostnameVerifier'),
    'Cleartext opt-in code':    re.compile(r'Landroid/net/NetworkSecurityPolicy;|cleartextTrafficPermitted|usesCleartextTraffic'),
    'Crypto primitives':        re.compile(r'Ljavax/crypto/(Cipher|SecretKeySpec|IvParameterSpec|KeyGenerator|Mac);'),
    'AES/GCM/RSA usage':        re.compile(r'"AES(?:/[^"]*)?"|"RSA(?:/[^"]*)?"|"DESede"|"PBKDF2[^"]*"'),
    'Android Keystore':         re.compile(r'Landroid/security/keystore/'),
    'SharedPreferences':        re.compile(r'Landroid/content/SharedPreferences;'),
    'EncryptedSharedPrefs':     re.compile(r'Landroidx/security/crypto/'),
    'SQLite / Room':            re.compile(r'Landroid/database/sqlite/SQLiteDatabase;|Landroidx/room/'),
    'Firebase Auth':            re.compile(r'Lcom/google/firebase/auth/'),
    'Firestore':                re.compile(r'Lcom/google/firebase/firestore/'),
    'Crashlytics':              re.compile(r'Lcom/google/firebase/crashlytics/'),
    'FCM':                      re.compile(r'Lcom/google/firebase/messaging/'),
    'Google Sign-In':           re.compile(r'Lcom/google/android/gms/auth/api/signin/'),
    'Maps SDK':                 re.compile(r'Lcom/google/android/gms/maps/'),
    'Places API':               re.compile(r'Lcom/google/android/libraries/places/'),
    'Facebook SDK':             re.compile(r'Lcom/facebook/(?!imagepipeline)[a-z]+/'),
    'Twitter SDK':              re.compile(r'Lcom/twitter|api.twitter.com'),
    'WhatsApp integration':     re.compile(r'api\.whatsapp\.com|wa\.me'),
    'Razorpay':                 re.compile(r'Lcom/razorpay/|razorpay_'),
    'Location/Fused':           re.compile(r'Lcom/google/android/gms/location/'),
    'Biometric / OTP':          re.compile(r'BiometricPrompt|SmsRetriever|otp|OTP'),
    'Camera/Capture':           re.compile(r'Lcom/github/drjacky/imagepicker|Landroid/hardware/camera2/'),
    'Barcode/QR':               re.compile(r'Lcom/google/zxing/|[Qq]r[Cc]ode'),
    'Dagger/Hilt':              re.compile(r'Ldagger/'),
    'WorkManager':              re.compile(r'Landroidx/work/'),
    'Pdf/Office rendering':      re.compile(r'Lcom/itextpdf/'),
    'Excel/POI':                re.compile(r'Lorg/apache/poi/'),
    'Fresco image':             re.compile(r'Lcom/facebook/fresco/'),
    'AnyChart':                 re.compile(r'Lcom/anychart/'),
    'PAIRIP VM encryption':     re.compile(r'Lcom/pairip/vmencryption/'),
    'Accessibility concern':    re.compile(r'AccessibilityService|FLAG_REQUEST_FILTER_KEY_EVENTS'),
}

cap = collections.defaultdict(set)   # marker -> set of classes
files = []
for d in sorted(os.listdir(SMALI)):
    sub = os.path.join(SMALI, d)
    if os.path.isdir(sub):
        for root, _, fns in os.walk(sub):
            for fn in fns:
                if fn.endswith('.smali'):
                    files.append(os.path.join(root, fn))

print('scanning', len(files), 'files...')
for fp in files:
    try:
        txt = open(fp, 'r', encoding='utf-8', errors='replace').read()
    except Exception:
        continue
    m = CLASS_RE.search(txt)
    cls = m.group(1) if m else '?'
    if owned(cls):
        parts = cls[1:].split('/')
        for depth in (1, 2, 3):
            p = '/'.join(parts[:depth])
            pkg_count[p] += 1
        if len(pkg_klasses[parts[0]]) < 3:
            pkg_klasses[parts[0]].append(cls)
    for name, rx in MARKERS.items():
        if rx.search(txt):
            cap[name].add(cls)

print()
print('=' * 74)
print('APP-OWNED NAMESPACE MAP (top 45 by class count)')
print('=' * 74)
for p, c in pkg_count.most_common(45):
    if c >= 3:
        print(f'{c:6d}  {p}')

print()
print('=' * 74)
print('CAPABILITY / SDK MATRIX')
print('=' * 74)
for name in MARKERS:
    s = cap.get(name, set())
    status = 'YES' if s else 'no'
    print(f'  [{status:3}] {name:34} classes={len(s)}')
    if s and name in ('Trust-all / bypass', 'JS bridge (@JavascriptInterface)',
                      'SSL/TLS custom', 'Cleartext opt-in code'):
        for c in sorted(s)[:6]:
            print(f'          {c}')

# ---- 3. native libs & assets ----
print()
print('=' * 74)
print('NATIVE LIBRARIES')
print('=' * 74)
lib = os.path.join(UNP, 'lib')
if os.path.isdir(lib):
    for abi in sorted(os.listdir(lib)):
        d = os.path.join(lib, abi)
        if os.path.isdir(d):
            tot = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d))
            names = sorted(os.listdir(d))
            print(f'  [{abi}]  {len(names)} libs, {tot/1048576:.1f} MB')
            for n in names:
                print(f'        {n}  ({os.path.getsize(os.path.join(d,n))/1024:.0f} KB)')

print()
print('=' * 74)
print('ASSETS (top-level)')
print('=' * 74)
a = os.path.join(UNP, 'assets')
if os.path.isdir(a):
    def walk(p, pre=''):
        ents = []
        for e in sorted(os.listdir(p)):
            fp = os.path.join(p, e)
            if os.path.isdir(fp):
                ents.extend(walk(fp, pre + e + '/'))
            else:
                ents.append((pre + e, os.path.getsize(fp)))
        return ents
    ents = walk(a)
    print(f'  total asset files: {len(ents)}')
    for n, s in sorted(ents, key=lambda x: -x[1])[:40]:
        print(f'  {s/1024:10.0f} KB  {n}')

# ---- 4. save capability matrix ----
with open(os.path.join(OUT, 'capability_matrix.txt'), 'w', encoding='utf-8') as f:
    for name in MARKERS:
        f.write(f'{name}\t{len(cap.get(name,set()))}\n')
with open(os.path.join(OUT, 'package_map.txt'), 'w', encoding='utf-8') as f:
    for p, c in pkg_count.most_common():
        f.write(f'{c}\t{p}\n')
print()
print('saved capability_matrix.txt + package_map.txt')
