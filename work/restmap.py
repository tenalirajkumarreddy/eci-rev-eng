"""Parse a smali Retrofit interface into method -> route/body/headers.

Usage: python work/restmap.py <RestClient.smali> [filter-substring]
"""
import re
import sys

METHOD_RE = re.compile(r"^\.method public abstract ([^(]+)\(([^)]*)\)")
FIELD_RE = re.compile(r"\.field (?:public |private )?([A-Za-z0-9_$]+):")
PARAM_TYPE_RE = re.compile(r"L([A-Za-z0-9_/$]+);")
PARAM_NAME_RE = re.compile(r'"(?:p\d+|hState|request|bearer|atknBand|rtknBand|body)"')


def parse(path, filt=None):
    txt = open(path, encoding="utf-8", errors="replace").read()
    blocks = txt.split("\n.method ")
    rows = []
    for b in blocks[1:]:
        m = METHOD_RE.match(".method " + b) or METHOD_RE.match(b)
        if not m:
            continue
        name, params = m.group(1), m.group(2)
        types = []
        for t in PARAM_TYPE_RE.finditer(params):
            t = t.group(1)
            if t.startswith("retrofit2/http/"):
                continue
            types.append(t)
        body_type = next((t for t in types if "models/" in t or "DataRepository" in t or "com/eci" in t), None)
        route = None
        rmethod = None
        for mm in re.finditer(r'runtime Lretrofit2/http/(GET|POST|PUT|DELETE|HTTP);\s*\n\s*value = "([^"]+)"', b):
            rmethod, route = mm.group(1), mm.group(2)
        if not route:
            for mm in re.finditer(r'runtime Lretrofit2/http/HTTP;\s*\n(?:\s*\w+ = [^\n]+\n)+\s*method = "([A-Z]+)"\s*\n\s*path = "([^"]+)"', b):
                rmethod, route = mm.group(1), mm.group(2)
        headers = re.findall(r'http/Header;\s*\n\s*value = "([^"]+)"', b)
        if filt and filt.lower() not in (route or "").lower() and filt.lower() not in name.lower():
            continue
        rows.append({"method": name, "http": rmethod, "path": route,
                     "body": body_type, "headers": headers})
    return rows


def dump_fields(smali_path):
    txt = open(smali_path, encoding="utf-8", errors="replace").read()
    return FIELD_RE.findall(txt)


def main():
    path = sys.argv[1]
    filt = sys.argv[2] if len(sys.argv) > 2 else None
    for r in parse(path, filt):
        print("%-42s %-5s %-52s body=%s" % (
            r["method"], r["http"] or "?", r["path"] or "?", r["body"] or "-"))
        if r["headers"]:
            print("     headers:", ", ".join(r["headers"]))


if __name__ == "__main__":
    main()
