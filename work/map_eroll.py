import os, re, collections

BASE = r'C:\Users\rajku\Documents\eci rev eng'
SMALI = os.path.join(BASE, 'work', 'smali')

# 1) list the eRoll feature package (non-inner classes only for clarity)
eroll = []
for root, _, fns in os.walk(SMALI):
    for fn in fns:
        if fn.endswith('.smali') and 'features/eRoll' in root.replace('\\', '/'):
            rel = os.path.relpath(os.path.join(root, fn), SMALI).replace('\\', '/')
            cls = re.sub(r'^smali(_classes\d+)?/', '', rel)[:-6]
            eroll.append(cls)

print('=' * 74)
print('features/eRoll PACKAGE - top-level classes')
print('=' * 74)
top = [c for c in sorted(eroll) if c.count('$') == 0]
for c in top:
    print('  ', c)
print()
print('total eRoll classes (incl. inner/anonymous):', len(eroll))

# 2) DataRepository - the API layer
print()
print('=' * 74)
print('DataRepository STRUCTURE')
print('=' * 74)
dr = collections.defaultdict(list)
for root, _, fns in os.walk(SMALI):
    for fn in fns:
        if fn.endswith('.smali') and 'DataRepository' in root.replace('\\', '/'):
            rel = os.path.relpath(os.path.join(root, fn), SMALI).replace('\\', '/')
            cls = re.sub(r'^smali(_classes\d+)?/', '', rel)[:-6]
            if cls.count('$') == 0:
                dr['top'].append(cls)
for c in sorted(dr['top']):
    print('  ', c)
