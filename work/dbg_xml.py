import xml.etree.ElementTree as ET

M = r'C:\Users\rajku\Documents\eci rev eng\work\apktool_res\AndroidManifest.xml'
AND = '{http://schemas.android.com/apk/res/android}'
root = ET.parse(M).getroot()
app = root.find('application')

print('AND prefix resolves:', repr(AND))
print('root attrib keys:', list(root.attrib.keys()))
print('app attrib keys:', list(app.attrib.keys())[:8])

acts = app.findall('activity')
print('activity count:', len(acts))
print('first activity name attr:', repr(acts[0].get(AND + 'name')))
print('first activity attribs:', acts[0].attrib)

# find one that has children
for a in acts:
    if len(list(a)) > 0:
        print()
        print('activity WITH children:', a.get(AND + 'name'))
        for c in a:
            print('   child tag =', repr(c.tag), ' attribs =', c.attrib)
            for g in c:
                print('        grandchild tag =', repr(g.tag), g.attrib)
        break

# count exported=true
exp_true = [a.get(AND + 'name') for a in acts if a.get(AND + 'exported') == 'true']
print()
print('activities with exported="true":', len(exp_true))
for n in exp_true[:20]:
    print('   ', n)
