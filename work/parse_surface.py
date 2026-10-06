import xml.etree.ElementTree as ET
import json, os, collections

M = r'C:\Users\rajku\Documents\eci rev eng\work\apktool_res\AndroidManifest.xml'
OUT = r'C:\Users\rajku\Documents\eci rev eng\out'
AND = '{http://schemas.android.com/apk/res/android}'
root = ET.parse(M).getroot()
app = root.find('application')
def a(e, n): return e.get(AND + n)

def ifaces(e):
    out = []
    for ifl in e.findall('intent-filter'):
        acts = [a(x, 'name') for x in ifl.findall('action')]
        cats = [a(x, 'name') for x in ifl.findall('category')]
        datas = []
        for d in ifl.findall('data'):
            dd = {k: d.get(AND + k) for k in ('scheme','host','port','path','pathPrefix','pathPattern','mimeType') if d.get(AND + k)}
            if dd: datas.append(dd)
        out.append((acts, cats, datas))
    return out

print('=' * 72)
print('EXPORTED COMPONENTS  (attack surface)')
print('=' * 72)
exported = []
for tag in ('activity','activity-alias','service','receiver','provider'):
    for e in app.findall(tag):
        nm = a(e,'name') or ''
        exp = a(e,'exported')
        has_if = e.find('intent-filter') is not None
        # implicit: exported defaults true when intent-filter present (pre-12)
        eff = exp
        if eff is None:
            eff = 'true (implicit, has intent-filter)' if has_if else 'false (implicit)'
        if has_if or exp == 'true':
            exported.append((tag, nm, eff, ifaces(e), a(e,'permission'), a(e,'authorities')))
for tag, nm, eff, ifs, perm, auth in exported:
    print(f'[{tag}] {nm}')
    print(f'    exported = {eff}')
    if auth: print(f'    authorities = {auth}')
    if perm: print(f'    permission = {perm}')
    for acts, cats, datas in ifs:
        if acts: print(f'    action  = {acts}')
        if cats: print(f'    category= {cats}')
        for d in datas:
            print(f'    data    = {json.dumps(d)}')
    print()

print()
print('=' * 72)
print('DEEPLINK SCHEMES (all)')
print('=' * 72)
schemes = collections.Counter()
deep = []
for tag in ('activity','activity-alias'):
    for e in app.findall(tag):
        for acts, cats, datas in ifaces(e):
            for d in datas:
                sc = d.get('scheme')
                if sc:
                    schemes[sc] += 1
                    deep.append((a(e,'name'), sc, d.get('host'), d.get('path'), d.get('pathPrefix')))
for s, c in schemes.most_common():
    print(f'  {c:4d}  {s}://')
print()
print('--- deeplink routes ---')
seen = set()
for nm, sc, h, p, pp in deep:
    k = (sc, h, p, pp)
    if k in seen: continue
    seen.add(k)
    print(f'  {sc}://{h or ""}{p or pp or ""}   ->  {nm}')

print()
print('=' * 72)
print('PROVIDERS')
print('=' * 72)
for e in app.findall('provider'):
    print(f'  {a(e,"name")}')
    print(f'      authorities={a(e,"authorities")} exported={a(e,"exported")} '
          f'grantUri={a(e,"grantUriPermissions")} perm={a(e,"permission")}')
    gp = a(e,'grantUriPermissions')
    if gp:
        for d in e.findall('grant-uri-permission'):
            print(f'      grant-uri: {d.get(AND+"path")} / {d.get(AND+"pathPrefix")} / {d.get(AND+"pathPattern")}')
    for pp in e.findall('path-permission'):
        print(f'      path-perm: {pp.get(AND+"path")} -> {pp.get(AND+"permission")}')

print()
print('=' * 72)
print('SERVICES')
print('=' * 72)
for e in app.findall('service'):
    print(f'  {a(e,"name")}  exported={a(e,"exported")} perm={a(e,"permission")} fg={a(e,"foregroundServiceType")}')

print()
print('=' * 72)
print('RECEIVERS')
print('=' * 72)
for e in app.findall('receiver'):
    print(f'  {a(e,"name")}  exported={a(e,"exported")} perm={a(e,"permission")} enabled={a(e,"enabled")}')
    for acts, cats, datas in ifaces(e):
        if acts: print(f'      action: {acts}')

print()
print('=' * 72)
print('META-DATA')
print('=' * 72)
for e in app.findall('meta-data'):
    print(f'  {a(e,"name")} = {a(e,"value")}  (resource={e.get(AND+"resource")})')

print()
print('=' * 72)
print('QUERIES (package visibility)')
print('=' * 72)
for q in root.findall('queries'):
    for p in q.findall('package'):
        print(f'  package: {p.get(AND+"name")}')
    for i in q.findall('intent'):
        acts = [a(x,'name') for x in i.findall('action')]
        print(f'  intent: {acts}')

print()
print('=' * 72)
print('CUSTOM PERMISSIONS DECLARED')
print('=' * 72)
for p in root.findall('permission'):
    print(f'  {a(p,"name")}  protectionLevel={a(p,"protectionLevel")}')
