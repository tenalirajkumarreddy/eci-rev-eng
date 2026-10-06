import os, struct

p = r'C:\Users\rajku\Documents\eci rev eng\base.apk'
size = os.path.getsize(p)
with open(p, 'rb') as f:
    tail_len = min(size, 65536 + 22)
    f.seek(size - tail_len)
    tail = f.read(tail_len)
    idx = tail.rfind(b'PK\x05\x06')
    cd_off = struct.unpack('<I', tail[idx + 16:idx + 20])[0]
    f.seek(cd_off - 24)
    footer = f.read(24)
    blk_size = struct.unpack('<Q', footer[0:8])[0]
    blk_start = cd_off - blk_size - 8
    f.seek(blk_start)
    blk = f.read(blk_size + 8)

# content region: skip leading size(8) and trailing size(8)+magic(16)
body = blk[8:len(blk) - 24]
KNOWN = {
    0x7109871a: 'APK Signature Scheme v2',
    0xf05368c0: 'APK Signature Scheme v3',
    0x1b93ad61: 'APK Signature Scheme v3.1',
    0x42726577: 'Padding block',
    0x6dff800d: 'Source stamp (v2)',
    0x2b09189e: 'Source stamp (v1)',
    0x2146444e: 'Google Play metadata',
    0x504b4453: 'Dependency info (Play)',
    0x71777777: 'Unknown-71777777',
    0x7f4f7f4f: 'Verity padding',
    0xb7a1b2c3: 'Verity digest',
}
print('APK Signing Block ID-value pairs:')
print('=' * 62)
off = 0
found = []
while off + 12 <= len(body):
    pair_len = struct.unpack('<Q', body[off:off + 8])[0]
    if pair_len == 0 or off + 8 + pair_len > len(body):
        break
    pid = struct.unpack('<I', body[off + 8:off + 12])[0]
    val = body[off + 12:off + 8 + pair_len]
    name = KNOWN.get(pid, 'UNKNOWN')
    print(f'  id=0x{pid:08x}  len={pair_len:>8d}  {name}')
    found.append((pid, name, val))
    off += 8 + pair_len

print()
print('SIGNATURE SCHEMES PRESENT:')
for pid, name, val in found:
    if 'Signature Scheme' in name:
        print(f'  - {name}')

# If v2/v3 present, try to parse signer certs to identify the signing identity
import hashlib
for pid, name, val in found:
    if pid == 0x7109871a or pid == 0xf05368c0:
        # signers are length-prefixed sequences of length-prefixed X.509 certs
        try:
            so = 0
            slen = struct.unpack('<I', val[so:so+4])[0]; so += 4
            end = so + slen
            while so < end:
                dlen = struct.unpack('<I', val[so:so+4])[0]; so += 4
                print()
                print(f'  [{name}] signer data len={dlen}, cert-set sha256={hashlib.sha256(val[so-dlen:so-dlen+dlen]).hexdigest()[:32]}')
                so += dlen
            # walk cert sequence
            cs = so
            print(f'  [{name}] raw value length = {len(val)}')
        except Exception as e:
            print('  parse note:', e)

# dump v2/v3 value to disk for external tooling (apksigner / openssl)
outdir = r'C:\Users\rajku\Documents\eci rev eng\out'
for pid, name, val in found:
    if 'Signature Scheme' in name:
        fn = os.path.join(outdir, 'sigblock_' + name.split()[-1] + '.bin')
        open(fn, 'wb').write(val)
        print('wrote', fn, len(val))
