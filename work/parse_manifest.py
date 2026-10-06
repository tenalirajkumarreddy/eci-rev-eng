import xml.etree.ElementTree as ET
import collections, os, re, json

M = r'C:\Users\rajku\Documents\eci rev eng\work\apktool_res\AndroidManifest.xml'
AND = '{http://schemas.android.com/apk/res/android}'
tree = ET.parse(M)
root = tree.getroot()

def a(el, name):
    return el.get(AND + name)

print('=' * 70)
print('APP IDENTITY')
print('=' * 70)
for k in ('package', 'versionCode', 'versionName', 'compileSdkVersion',
          'platformBuildVersionCode', 'platformBuildVersionName', 'installLocation'):
    v = a(root, k) or (root.get(k) if k == 'package' else None)
    if k == 'package':
        v = root.get('package')
    if v:
        print(f'  {k:28} {v}')

us = root.find('uses-sdk')
if us is not None:
    print(f'  {"minSdkVersion":28} {a(us, "minSdkVersion")}')
    print(f'  {"targetSdkVersion":28} {a(us, "targetSdkVersion")}')

app = root.find('application')
print()
print('=' * 70)
print('APPLICATION')
print('=' * 70)
for k in ('label', 'name', 'debuggable', 'allowBackup', 'usesCleartextTraffic',
          'networkSecurityConfig', 'extractNativeLibs', 'largeHeap',
          'appComponentFactory', 'permission', 'theme', 'icon'):
    v = a(app, k) if app is not None else None
    if v:
        print(f'  {k:28} {v}')

# ---------------- PERMISSIONS ----------------
perms = [a(p, 'name') for p in root.findall('uses-permission')]
perms += [a(p, 'name') for p in root.findall('uses-permission-sdk-23')]
perms = sorted(set(x for x in perms if x))
DANGEROUS = {
 'READ_CONTACTS','WRITE_CONTACTS','GET_ACCOUNTS','CAMERA','READ_CALENDAR','WRITE_CALENDAR',
 'ACCESS_FINE_LOCATION','ACCESS_COARSE_LOCATION','ACCESS_BACKGROUND_LOCATION','RECORD_AUDIO',
 'READ_PHONE_STATE','READ_PHONE_NUMBERS','CALL_PHONE','ANSWER_PHONE_CALLS','READ_CALL_LOG',
 'WRITE_CALL_LOG','ADD_VOICEMAIL','USE_SIP','PROCESS_OUTGOING_CALLS','BODY_SENSORS',
 'SEND_SMS','RECEIVE_SMS','READ_SMS','RECEIVE_WAP_PUSH','RECEIVE_MMS','READ_EXTERNAL_STORAGE',
 'WRITE_EXTERNAL_STORAGE','READ_MEDIA_IMAGES','READ_MEDIA_VIDEO','READ_MEDIA_AUDIO',
 'POST_NOTIFICATIONS','ACTIVITY_RECOGNITION','NEARBY_WIFI_DEVICES','BLUETOOTH_SCAN',
 'READ_MEDIA_VISUAL_USER_SELECTED','MANAGE_EXTERNAL_STORAGE','QUERY_ALL_PACKAGES',
}
print()
print('=' * 70)
print(f'PERMISSIONS ({len(perms)})')
print('=' * 70)
for p in perms:
    short = p.split('.')[-1]
    tag = ''
    if short in DANGEROUS or 'DANGEROUS' in p:
        tag = '   <<< DANGEROUS/RUNTIME'
    print(f'  {p}{tag}')

# ---------------- FEATURES ----------------
feats = [(a(f,'name'), a(f,'required')) for f in root.findall('uses-feature')]
if feats:
    print()
    print('=' * 70)
    print('FEATURES')
    print('=' * 70)
    for n, r in feats:
        print(f'  {n}   required={r}')

# ---------------- COMPONENTS ----------------
def dump(tagname, extra=()):
    parent = app if app is not None else root
    els = parent.findall(tagname)
    if not els:
        return
    print()
    print('=' * 70)
    print(f'{tagname.upper()} ({len(els)})')
    print('=' * 70)
    for e in els:
        nm = a(e, 'name') or ''
        bits = []
        for x in extra:
            v = a(e, x)
            if v and v != 'false':
                bits.append(f'{x}={v}')
        exp = a(e, 'exported')
        if exp is not None:
            bits.append(f'exported={exp}')
        perm = a(e, 'permission') or a(e, 'readPermission') or a(e, 'writePermission')
        if perm:
            bits.append(f'perm={perm}')
        # intent filters -> actions/data
        for ifl in e.findall('intent-filter'):
            acts = [a(x, 'name') for x in ifl.findall('action')]
            categs = [a(x, 'name') for x in ifl.findall('category')]
            datas = []
            for d in ifl.findall('data'):
                dd = {k: d.get(AND + k) for k in ('scheme','host','port','path','pathPrefix','pathPattern','mimeType') if d.get(AND + k)}
                if dd:
                    datas.append(dd)
            if acts:
                bits.append('actions=' + ','.join(x.split('.')[-1] for x in acts if x))
            if categs:
                bits.append('cat=' + ','.join(x.split('.')[-1] for x in categs if x))
            if datas:
                bits.append('data=' + json.dumps(datas, separators=(',', ':')))
        line = f'  {nm}'
        if bits:
            line += '\n        ' + '\n        '.join(bits)
        print(line)

dump('activity', ('launchMode','taskAffinity','excludeFromRecents','screenOrientation','configChanges','exported'))
dump('activity-alias')
dump('service', ('process','foregroundServiceType'))
dump('receiver')
dump('provider', ('authorities','grantUriPermissions','multiprocess','initOrder'))
