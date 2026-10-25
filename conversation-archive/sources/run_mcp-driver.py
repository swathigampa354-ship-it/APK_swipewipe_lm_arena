#!/usr/bin/env python3
"""Drive jadx-headless-mcp for the Swipewipe resume. Resumable, append-only progress.

Framing: newline-delimited JSON-RPC 2.0 over the server's stdio, exactly as the recovered
driver did, but with one long-lived server for all calls and a 420 s init budget (the old
18x5s=90 s wait was shorter than the 94 s load, which produced NO_INIT_RESPONSE).
"""
import json, os, subprocess, sys, time, hashlib

HOME = os.environ.get("RECON_HOME", os.path.expanduser("~"))
WORK = os.environ.get("RECON_WORK", f"{HOME}/rea/swipewipe")
OUT  = f"{WORK}/mcp_out"
CLS  = f"{OUT}/classes"
JAR  = os.environ.get("RECON_JAR", f"{HOME}/.local/share/rea/android/jadx-headless-mcp-0.7.1-all.jar")
APK  = f"{WORK}/base.apk"
PKG  = "com.afternoonproducts.swipewipe"
LOGF = f"{WORK}/progress.jsonl"
DRIVER = f"{WORK}/driver.log"
_LOCK = None

def progress(step, ok, detail=""):
    line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "step": step,
                       "ok": bool(ok), "detail": str(detail)[:400]})
    with open(LOGF, "a") as fh:
        fh.write(line + "\n"); fh.flush(); os.fsync(fh.fileno())

def log(msg):
    with open(DRIVER, "a") as fh:
        fh.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

class MCP:
    def __init__(self, argv, init_budget=420):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=open(DRIVER, "ab"), bufsize=0, text=True)
        self.id = 0; self.init_budget = init_budget
    def send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n"); self.p.stdin.flush()
    def request(self, method, params=None, timeout=120):
        self.id += 1; rid = self.id
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self._await(rid, timeout)
    def notify(self, method, params=None):
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})
    def _await(self, rid, timeout):
        end = time.time() + timeout
        while time.time() < end:
            line = self.p.stdout.readline()
            if not line:
                if self.p.poll() is not None:
                    raise RuntimeError(f"server exited rc={self.p.returncode} awaiting id={rid}")
                continue
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try: msg = json.loads(line)
            except json.JSONDecodeError: continue
            if msg.get("id") == rid:
                if "error" in msg:
                    return {"isError": True, "content": [{"type": "text",
                              "text": json.dumps(msg["error"])[:4000]}]}
                return msg.get("result", {})
        raise TimeoutError(f"no response for id={rid} after {timeout}s")
    def tool(self, name, args=None, timeout=180):
        r = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout)
        return r
    def text_of(self, r):
        for c in (r.get("content") or []):
            if c.get("type") == "text":
                return c.get("text", "")
        return ""
    def stop(self):
        try: self.p.stdin.close()
        except Exception: pass
        try: self.p.terminate(); self.p.wait(timeout=15)
        except Exception:
            try: self.p.kill()
            except Exception: pass

def save(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(text)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(), len(text)

def main():
    # single-instance guard: the OpenCode agent may also launch this driver, and two
    # jadx servers at -Xmx2048m on this phone would OOM each other. Second run no-ops.
    global _LOCK
    import fcntl
    _LOCK = open(f"{WORK}/.driver.lock", "w")
    try:
        fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another driver instance holds .driver.lock; exiting without work")
        print("LOCK_HELD_BY_OTHER_RUN"); return 0
    mock = "--mock" in sys.argv
    os.makedirs(CLS, exist_ok=True); os.makedirs(f"{OUT}/xrefs", exist_ok=True)
    argv = [sys.executable, f"{WORK}/mock_server.py"] if mock else [
        "java", os.environ.get("RECON_XMX", "-Xmx2048m"), "-jar", JAR, "--transport", "stdio", "--apk", APK,
        "--resources", "none", "--include-packages", PKG + ",com.mwm.swipewipe", "--threads", "2",
        "--decompile-timeout-ms", "90000"]
    if not mock and not os.path.exists(JAR):
        progress("preflight", False, f"missing jar {JAR}"); print("MISSING_JAR"); return 2
    apk_sha = None
    if os.path.exists(APK):
        h = hashlib.sha256()
        with open(APK, "rb") as fh:
            for b in iter(lambda: fh.read(1 << 20), b""): h.update(b)
        apk_sha = h.hexdigest()
    with open(f"{WORK}/.driver.pid", "w") as fh:
        fh.write(str(os.getpid()))          # launch.sh kills by pidfile, not by pattern
    free_kb = 0
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                free_kb = int(line.split()[1]); break
    except OSError:
        pass
    heap_mb = 2048
    for tok in argv:
        if tok.startswith("-Xmx"):
            try:
                heap_mb = int(tok[4:].rstrip("mMkK"))
            except ValueError:
                pass
    min_free = int(os.environ.get("RECON_MIN_FREE_KB", str(int(heap_mb * 1024 * 1.35))))
    if not mock and free_kb and free_kb < min_free:
        # this phone was OOM-killed with the whole Termux group once already; do not
        # start a 2 GB heap when the kernel has less than ~3.1 GB to give
        log(f"refusing to start: MemAvailable={free_kb} kB < required {min_free} kB for -Xmx{heap_mb}m")
        print(f"LOW_MEMORY MemAvailable_kB={free_kb} need_kB={min_free}"); return 3
    if not mock:
        log(f"preflight MemAvailable={free_kb} kB")
    m = MCP(argv)
    summary = {"apk_sha256": apk_sha, "jar_sha256": None if mock else
               hashlib.sha256(open(JAR, "rb").read()).hexdigest(),
               "server": None, "load": None, "classes_total": 0, "decompiled_java": 0,
               "decompiled_smali": 0, "errors": [], "xref_calls": [], "unfinished": []}
    try:
        init = m.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "swipewipe-resume", "version": "1"}},
                        timeout=m.init_budget)
        summary["server"] = init.get("serverInfo")
        m.notify("notifications/initialized")
        progress("initialize", True, json.dumps(init.get("serverInfo")))
        tools = m.request("tools/list", {}, timeout=90)
        names = [t["name"] for t in tools.get("tools", [])]
        tl, _ = save(f"{OUT}/tools.json", json.dumps(tools, indent=2))
        progress("tools/list", True, f"{len(names)} tools, sha256 {tl[:12]}")
        st = m.text_of(m.tool("status", {}, timeout=60))
        log("status: " + st[:400])
        sq = st.replace(" ", "")
        already = ("base.apk" in st) or ('"loaded":true' in sq and "swipewipe" in sq)
        if already:
            summary["load"] = "already loaded at server start via --apk (skipped re-index)"
            progress("load_apk", True, "skipped: status reports the CLI-loaded APK")
        if not mock and not already:
            for extra in ({"path": APK, "include_packages": PKG + ",com.mwm.swipewipe",
                          "resources": "lite", "threads": 2, "code_cache_size": 128},
                         {"path": APK}):
                r = m.tool("load_apk", extra, timeout=m.init_budget)
                t = m.text_of(r)
                if not r.get("isError"):
                    summary["load"] = t[:2000]
                    progress("load_apk", True, t[:180]); log(f"load_apk ok with {list(extra)}"); break
                log(f"load_apk args {list(extra)} -> {t[:120]}")
            else:
                progress("load_apk", False, "all arg shapes rejected")
        for call, fname, extra in (("get_app_info", "app_info.json", {}),
                                   ("get_android_manifest", "manifest.json",
                                    {"section": "application", "max_bytes": 60000})):
            try:
                r = m.tool(call, extra, timeout=180); tx = m.text_of(r)
                if r.get("isError"):
                    summary["errors"].append({"call": call, "error": tx[:300]})
                    progress(call, False, tx[:160])
                else:
                    h, n = save(f"{OUT}/{fname}", tx); progress(call, True, f"{n} chars sha {h[:12]}")
            except Exception as exc:
                summary["errors"].append({"call": call, "error": repr(exc)[:200]}); progress(call, False, exc)
        classes = []
        inv = f"{WORK}/inputs/class-inventory.json"
        if os.path.exists(inv):
            try:
                d = json.load(open(inv))
                classes = d.get("classes") or d.get("items") or []
            except Exception: classes = []
        if not classes:
            for cn in ("get_class_list", "list_classes"):
                try:
                    r = m.tool(cn, {"package": PKG} if "list_classes" in cn else {"package_name": PKG}, 120)
                    t = json.loads(m.text_of(r)); classes = t.get("items") or t.get("classes") or []
                    if classes: break
                except Exception as exc: log(f"{cn}: {exc}")
        summary["classes_total"] = len(classes)
        progress("class inventory", bool(classes), f"{len(classes)} classes")
        done = set()
        for f in os.listdir(CLS):
            done.add(f.rsplit(".", 1)[0])
        for fqcn in classes:
            short = fqcn.replace(PKG + ".", "")
            if short in done: continue
            got = False
            try:
                r = m.tool("get_class_source", {"class_name": fqcn, "max_bytes": 400000}, timeout=240)
                t = m.text_of(r)
                if "jadx java-decompile failed" in t:   # server-side java->smali fallback
                    h, n = save(f"{CLS}/{short}.smali", t)
                    summary["decompiled_smali"] += 1
                    progress("class", True, f"{short}.smali (server fallback) {n} chars sha {h[:10]}")
                    continue
                lost = "Code restructure failed" in t or "JadxOverflow" in t or "/* compiled code */" in t
                if not r.get("isError") and len(t.strip()) > 40 and not lost:
                    h, n = save(f"{CLS}/{short}.java", t)
                    summary["decompiled_java"] += 1; got = True
                    progress("class", True, f"{short}.java {n} chars sha {h[:10]}")
                elif not r.get("isError") and lost:
                    raise RuntimeError("jadx lost code (restructure/overflow) -> smali")
                else:
                    raise RuntimeError(t[:200] or "empty")
            except Exception as exc:
                why = str(exc)[:200]
                try:
                    r = m.tool("get_smali_of_class", {"class_name": fqcn}, timeout=240)
                    t = m.text_of(r)
                    if not r.get("isError") and len(t.strip()) > 10:
                        h, n = save(f"{CLS}/{short}.smali", t)
                        summary["decompiled_smali"] += 1; got = True
                        progress("class", True, f"{short}.smali (java failed: {why[:90]}) sha {h[:10]}")
                except Exception as e2:
                    why += f" | smali: {e2}"
            if not got:
                summary["errors"].append({"class": fqcn, "error": why[:300]})
                progress("class", False, f"{short}: {why[:160]}")
        for call, args in (("get_xrefs_to_class", {"class_name": f"{PKG}.one_drive_data_source.OneDriveDataSourceManagerImpl"}),
                          ("get_xrefs_to_class", {"class_name": f"{PKG}.data.AppDatabase_Impl"}),
                          ("get_xrefs_to_method", {"class_name": f"{PKG}.one_drive_data_source.OneDriveDataSourceManagerImpl", "method_name": "deleteItemsBatch"}),
                          ("search_classes_by_keyword", {"keyword": "http://"}),
                          ("search_classes_by_keyword", {"keyword": "Retrofit"}),
                          ("search_classes_by_keyword", {"keyword": "allowBackup"})):
            key = f"{call}:{json.dumps(args, sort_keys=True)}"
            try:
                r = m.tool(call, args, timeout=200); t = m.text_of(r)
                fn = f"{OUT}/xrefs/{call}_{abs(hash(key)) % 10**6}.json"
                if r.get("isError"):
                    summary["errors"].append({"call": key, "error": t[:300]})
                else:
                    save(fn, t); summary["xref_calls"].append({"call": key, "file": os.path.basename(fn), "chars": len(t)})
                progress("xref", not r.get("isError"), key[:120])
            except Exception as exc:
                summary["errors"].append({"call": key, "error": repr(exc)[:200]})
    finally:
        m.stop()
        ok = summary["decompiled_java"] + summary["decompiled_smali"]
        if summary["classes_total"] and ok < summary["classes_total"]:
            summary["unfinished"].append(f"{summary['classes_total'] - ok} class(es) not written")
        save(f"{WORK}/summary.json", json.dumps(summary, indent=2, sort_keys=True))
        progress("summary", True, f"java={summary['decompiled_java']} smali={summary['decompiled_smali']} errors={len(summary['errors'])}")
        print(json.dumps({k: summary[k] for k in ("classes_total","decompiled_java","decompiled_smali")}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
