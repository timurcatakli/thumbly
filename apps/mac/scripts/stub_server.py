#!/usr/bin/env python3
"""A tiny fake Thumbly owner API for testing the Mac app's write flows without touching a real phone.

    python3 scripts/stub_server.py [port] [scenario]
    THUMBLY_API_URL=http://127.0.0.1:8799 build/Thumbly.app/Contents/MacOS/Thumbly

Scenarios: ready (default, with one pending request), paused, trust, devmode, unbound.
GET /_log returns every POST received; POST /_scenario {"name": ...} switches scenario.
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
LOG = []


def fresh(scenario="ready"):
    paused = scenario == "paused"
    scenario = "ready" if paused else scenario
    connected = scenario == "ready"
    now = time.time()
    return {
        "scenario": scenario,
        "paused": paused,
        "ops": [{"id": f"op{i}", "kind": k, "client": "Claude Code", "state": st, "created": now - i * 40,
                 "updated": now - i * 40 + 0.8, **extra}
                for i, (k, st, extra) in enumerate([
                    ("tap", "SUCCEEDED", {}), ("acquire", "SUCCEEDED", {}),
                    ("launch_app", "SUCCEEDED", {"app": "com.burbn.instagram"}), ("swipe", "SUCCEEDED", {}),
                    ("type_text", "FAILED", {"error_code": "PAUSED"}), ("release", "SUCCEEDED", {}),
                    ("open_url", "SUCCEEDED", {"app": "com.apple.Preferences", "url": "App-prefs:PASSWORDS"}),
                    ("tap", "QUEUED", {})])],
        "bound": scenario not in ("unbound", "trust"),
        "pairing": {"unbound": "no_phone", "trust": "trust_pending"}.get(scenario, "bound"),
        "conn": {"ready": "connected", "devmode": "developer_mode_required",
                 "trust": "trust_required", "unbound": "waiting_for_device"}[scenario],
        "consents": [{"handle": "h-demo", "client_name": "Claude Desktop", "client_id": "c-new",
                      "scopes": ["phone:read", "phone:control"], "issuer": "http://127.0.0.1:8771",
                      "expires_in": 540, "redirect_hosts": ["localhost"]}] if connected else [],
        "clients": [{"client_id": "c-1", "client_name": "Claude Code", "approved": True,
                     "scopes": ["phone:read", "phone:control"], "active_until": {"refresh": time.time() + 86400 * 30},
                     "renewed_while_used": True, "never_expires": False, "self": False, "last_used": time.time() - 300}],
    }


S = fresh()


def operations(path):
    ops = S["ops"]
    if "actions=1" in path:
        ops = [o for o in ops if o["kind"] not in ("acquire", "release")]
    return {"operations": ops, "has_more": False, "total": len(ops)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        c = S["conn"]
        routes = {
            "/api/overview": lambda: {"name": "Thumbly", "bound": S["bound"], "cable": S["scenario"] != "unbound",
                                      "usb_pairing": {"state": S["pairing"], "device": "stub", "last_error": None, "retry_in": None}},
            "/api/info": lambda: {"name": "Thumbly", "version": "0.0.0-stub", "issuer": "http://127.0.0.1:8771",
                                  "mcp_url": "http://127.0.0.1:8771/mcp", "public_url": None, "scopes": []},
            "/api/status": lambda: {"connection": {"state": c, "transport": "usb", "message": "Stub: " + c,
                                                   "product_version": "18.4", "developer_mode": S["scenario"] == "ready",
                                                   "ddi_mounted": True, "device_present": S["scenario"] != "unbound"},
                                    "control": {"active": False, "owned": False, "paused": S["paused"], "remaining_seconds": 0},
                                    "pending_consents": len(S["consents"])},
            "/api/consents": lambda: {"consents": S["consents"]},
            "/api/clients": lambda: {"clients": S["clients"]},
            "/api/operations": lambda: operations(self.path),
            "/api/discover": lambda: {"usb": [{"udid": "STUB-UDID"}]},
            "/api/diagnostics": lambda: {"stub": True, "at": time.time()},
            "/_log": lambda: {"posts": LOG},
        }
        if path in routes:
            self.send(routes[path]())
        else:
            self.send({"error": {"code": "NOT_FOUND", "message": path}}, 404)

    def do_POST(self):
        global S
        path = urlparse(self.path).path
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        LOG.append({"path": path, "body": body, "client": self.headers.get("x-thumbly-client")})
        if path == "/_scenario":
            S = fresh(body.get("name", "ready"))
            return self.send({"ok": True})
        if self.headers.get("x-thumbly-client") != "dashboard":
            return self.send({"error": {"code": "FORBIDDEN", "message": "missing x-thumbly-client"}}, 403)
        if path == "/api/pause":
            S["paused"] = True
            return self.send({"paused": True})
        if path == "/api/resume":
            S["paused"] = False
            return self.send({"kind": "resume", "state": "SUCCEEDED", "result": {"paused": False}})
        if path == "/api/operations/clear":
            kept = [o for o in S["ops"] if o["state"] in ("QUEUED", "DISPATCHED")]
            cleared, S["ops"] = len(S["ops"]) - len(kept), kept
            return self.send({"cleared": cleared})
        if path.startswith("/api/consents/"):
            handle = path.rsplit("/", 1)[1]
            match = [x for x in S["consents"] if x["handle"] == handle]
            if not match:
                return self.send({"error": {"code": "CONSENT_EXPIRED", "message": "That request expired"}}, 404)
            S["consents"].remove(match[0])
            if body.get("decision") == "allow":
                S["clients"].append({"client_id": match[0]["client_id"], "client_name": match[0]["client_name"],
                                     "approved": True, "scopes": match[0]["scopes"],
                                     "never_expires": body.get("window") == "never", "self": False})
            return self.send({"decided": body.get("decision")})
        if path.startswith("/api/clients/") and path.endswith("/revoke"):
            cid = path.split("/")[3]
            S["clients"] = [x for x in S["clients"] if x["client_id"] != cid]
            return self.send({"revoked": cid})
        if path == "/api/setup/developer_mode":
            S["conn"], S["scenario"] = "connected", "ready"
            return self.send({"state": "SUCCEEDED"})
        if path == "/api/setup/unbind":
            S = fresh("unbound")
            return self.send({"state": "SUCCEEDED"})
        if path == "/api/reset":
            if str(body.get("name", "")).strip().lower() != "thumbly":
                return self.send({"error": {"code": "RESET_NAME_MISMATCH", "message": "Type Thumbly to confirm"}}, 400)
            S = fresh("unbound")
            return self.send({"state": "resetting"}, 202)
        self.send({"error": {"code": "NOT_FOUND", "message": path}}, 404)


if __name__ == "__main__":
    if len(sys.argv) > 2:
        S = fresh(sys.argv[2])
    print(f"Thumbly stub on http://127.0.0.1:{PORT} ({S['scenario']})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
