import os, struct, subprocess

BASE = r'C:\Users\rajku\Documents\eci rev eng'
p = os.path.join(BASE, 'base.apk')
outdir = os.path.join(BASE, 'out')

size = os.path.getsize(p)
with open(p, 'rb') as f:
    tlen = min(size, 65536 + 22)
    f.seek(size - tlen); tail = f.read(tlen)
    i = tail.rfind(b'PK\x05\x06')
    cd_off = struct.unpack('<I', tail[i+16:i+20])[0]
    f.seek(cd_off - 24); footer = f.read(24)
    blk_size = struct.unpack('<Q', footer[0:8])[0]
    f.seek(cd_off - blk_size - 8); blk = f.read(blk_size + 8)
body = blk[8:len(blk) - 24]

off = 0
v2 = None
while off + 12 <= len(body):
    pl = struct.unpack('<Q', body[off:off+8])[0]
    if pl == 0 or off + 8 + pl > len(body):
        break
    pid = struct.unpack('<I', body[off+8:off+12])[0]
    if pid == 0x7109871a:
        v2 = body[off+12:off+8+pl]
    off += 8 + pl

# Scan for DER Certificate: 30 82 LL LL  30 82 ... (tbs) ... 30 82 ... (alg) ... 03 82 ... (sig)
cands = []
pos = 0
while True:
    k = v2.find(b'\x30\x82', pos)
    if k < 0 or k + 4 > len(v2):
        break
    ln = struct.unpack('>H', v2[k+2:k+4])[0]
    total = 4 + ln
    if k + total <= len(v2):
        blob = v2[k:k+total]
        # certificate must contain an inner SEQUENCE and end with BIT STRING
        if b'\x03\x82' in blob[-80:] and blob.count(b'\x30\x82') >= 3:
            cands.append((k, blob))
    pos = k + 1

print('DER certificate candidates:', len(cands))
kt = r'C:\Program Files\Eclipse Adoptium\jdk-21.0.7.6-hotspot\bin\keytool.exe'
best = None
for idx, (k, blob) in enumerate(cands):
    fn = os.path.join(outdir, f'cert_try_{idx}.der')
    open(fn, 'wb').write(blob)
    r = subprocess.run([kt, '-printcert', '-file', fn], capture_output=True, text=True)
    if r.returncode == 0 and 'Owner' in r.stdout:
        print()
        print('=' * 66)
        print(f'VALID CERTIFICATE at signing-block offset {k}, len {len(blob)}')
        print('=' * 66)
        print(r.stdout)
        best = (k, blob)
        break
    os.remove(fn)

if not best:
    print('no valid certificate recovered via DER scan')
