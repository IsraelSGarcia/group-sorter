import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

HOST = "127.0.0.1"
PORT = 8085
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GROUPS_DIR = os.path.join(BASE_DIR, "groups")

last_heartbeat = time.time()
has_connected = False
pending_shutdown = None
lock = threading.Lock()

class GroupSorterHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        # Suppress logging in background/windowless execution
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/groups":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()

            files = []
            if os.path.exists(GROUPS_DIR):
                for f in sorted(os.listdir(GROUPS_DIR)):
                    if f.lower().endswith(".json"):
                        files.append({
                            "filename": f,
                            "name": os.path.splitext(f)[0]
                        })
            self.wfile.write(json.dumps(files, ensure_ascii=False).encode("utf-8"))
            return

        super().do_GET()

    def do_POST(self):
        global last_heartbeat, has_connected, pending_shutdown
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/heartbeat":
            with lock:
                last_heartbeat = time.time()
                has_connected = True
                pending_shutdown = None
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if parsed.path == "/api/save":
            try:
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len)
                payload = json.loads(body.decode("utf-8"))
                filename = payload.get("filename", "").strip()
                data = payload.get("data")

                filename = os.path.basename(filename)
                if not filename.endswith(".json"):
                    filename += ".json"

                if not os.path.exists(GROUPS_DIR):
                    os.makedirs(GROUPS_DIR, exist_ok=True)

                filepath = os.path.join(GROUPS_DIR, filename)
                content = payload.get("content")
                if content:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(content)
                        if not content.endswith("\n"):
                            f.write("\n")
                else:
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                        f.write("\n")

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "saved", "filename": filename}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        if parsed.path == "/api/shutdown":
            with lock:
                # 1.5s grace period so reload/F5 doesn't kill the server before the new page connects
                pending_shutdown = time.time() + 1.5
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"shutting_down"}')
            return

        self.send_error(404, "Not Found")

def watchdog(server):
    global last_heartbeat, has_connected, pending_shutdown
    startup_time = time.time()
    while True:
        time.sleep(0.25)
        now = time.time()
        with lock:
            if pending_shutdown is not None and now >= pending_shutdown:
                break
            if has_connected:
                # Terminate server if no heartbeat received for > 4.5 seconds
                if now - last_heartbeat > 4.5:
                    break
            else:
                # Terminate server if initial browser connection never arrived within 15 seconds
                if now - startup_time > 15.0:
                    break

    try:
        server.shutdown()
    except Exception:
        pass
    os._exit(0)

def main():
    # If server is already running, open the browser and exit cleanly
    try:
        req = urllib.request.Request(f"http://{HOST}:{PORT}/api/heartbeat", method="POST")
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            if resp.status == 200:
                webbrowser.open(f"http://{HOST}:{PORT}/")
                sys.exit(0)
    except Exception:
        pass

    http.server.ThreadingHTTPServer.allow_reuse_address = True
    server = None
    active_port = PORT

    for p in range(PORT, PORT + 20):
        try:
            server = http.server.ThreadingHTTPServer((HOST, p), GroupSorterHandler)
            active_port = p
            break
        except OSError:
            continue

    if not server:
        sys.exit(1)

    t = threading.Thread(target=watchdog, args=(server,), daemon=True)
    t.start()

    webbrowser.open(f"http://{HOST}:{active_port}/")
    server.serve_forever()

if __name__ == "__main__":
    main()
