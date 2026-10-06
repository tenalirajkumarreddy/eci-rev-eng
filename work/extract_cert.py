import os, struct, re

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
    f.seek(cd_off - blk_size - 8)
    blk = f.read(blk_size + 8)
body = blk[8:len(blk) - 24]
off = 0
v2 = None
while off + 12 <= len(body):
    pair_len = struct.unpack('<Q', body[off:off + 8])[0]
    if pair_len == 0 or off + 8 + pair_len > len(body):
        break
    pid = struct.unpack('<I', body[off + 8:off + 12])[0]
    val = body[off + 12:off + 8 + pair_len]
    if pid == 0x7109871a:
        v2 = val
    off += 8 + pair_len

# In v2, walk to the signed-data blob which embeds the X.509 cert.
# Structure: signers(seq) -> signer -> signed data(len) -> digests, certificates
def walk(v):
    o = 0
    slen = struct.unpack('<I', v[o:o+4])[0]; o += 4
    dlen = struct.unpack('<I', v[o:o+4])[0]; o += 4
    signed = v[o:o+dlen]; o += dlen
    o += 4  # minSdk
    o += 4  # maxSdk
    sigslen = struct.unpack('<I', v[o:o+4])[0]; o += 4 + sigslen
    pklen = struct.unpack('<I', v[o:o+4])[0]; o += 4 + pklen
    return signed

signed = walk(v2)
# signed data: digests(seq), certificates(seq of cert)
o = 0
dlen2 = struct.unpack('<I', signed[o:o+4])[0]; o += 4 + dlen2
clen = struct.unpack('<I', signed[o:o+4])[0]; o += 4
certslen = struct.unpack('<I', signed[o:o+4])[0]; o += 4
certs = signed[o:o+certslen]

outdir = r'C:\Users\rajku\Documents\eci rev eng\out'
co = 0
n = 0
while co + 4 <= len(certs):
    cl = struct.unpack('<I', certs[co:co+4])[0]
    if cl == 0 or co + 4 + cl > len(certs):
        break
    cert = certs[co+4:co+4+cl]
    n += 1
    fn = os.path.join(outdir, f'signer_cert_{n}.der')
    open(fn, 'wb').write(cert)
    print('extracted cert', n, 'len', cl, '->', fn)
    co += 4 + cl
print('total certs:', n)
