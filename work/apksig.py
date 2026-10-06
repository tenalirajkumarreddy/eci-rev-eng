import os, struct, hashlib

p = r'C:\Users\rajku\Documents\eci rev eng\base.apk'
size = os.path.getsize(p)
with open(p, 'rb') as f:
    # find EOCD (PK\x05\x06) scanning backwards
    tail_len = min(size, 65536 + 22)
    f.seek(size - tail_len)
    tail = f.read(tail_len)
    idx = tail.rfind(b'PK\x05\x06')
    if idx < 0:
        print('EOCD not found')
    else:
        eocd_abs = size - tail_len + idx
        print('EOCD at offset   :', eocd_abs)
        cd_off = struct.unpack('<I', tail[idx + 16:idx + 20])[0]
        print('central dir off  :', cd_off)
        comment_len = struct.unpack('<H', tail[idx + 20:idx + 22])[0]
        print('zip comment len  :', comment_len)

        # APK Signing Block sits immediately before central directory
        if cd_off >= 24:
            f.seek(cd_off - 24)
            footer = f.read(24)
            blk_size = struct.unpack('<Q', footer[0:8])[0]
            magic = footer[8:24]
            print()
            print('block magic      :', magic)
            if magic == b'APK Sig Block 42':
                blk_start = cd_off - blk_size - 8
                f.seek(blk_start)
                blk = f.read(blk_size + 8)
                print('signing block    : offset=%d size=%d' % (blk_start, blk_size))
                print('  sha256         :', hashlib.sha256(blk).hexdigest())
                for label, magic_id in (('v2', b'\x71\x09\x87\x1a'),
                                       ('v3', b'\xf0\x53\x68\xc0'),
                                       ('v3.1', b'\x1b\x93\xad\x61'),
                                       ('verity', b'\xb7\x1a\xd9\x1e'),
                                       ('source stamp v2', b'\x2b\x91\x00\x5b'),
                                       ('dep blocks', b'\xfb\xc5\x9d\x0b')):
                    print(f'  contains {label:16}: {magic_id in blk}')
            else:
                print('No APK Signing Block before central directory')
                print('footer hex:', footer.hex())
