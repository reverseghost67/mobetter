"""Keep MoBetter running: start server.py, and if it ever exits, log why and start it again.

    pythonw supervisor.py --lan                  (same arguments as server.py; they are passed through)
    pythonw supervisor.py --lan --port 8781

Logs (in data\\):
    server.log       everything MoBetter prints (rotated at 5 MB, one old copy kept)
    crash.log        one entry per stop: time, exit code, how long it ran, the last lines of its output
Restarts after 5 seconds. If it keeps dying (5 stops within 2 minutes) it waits 60 seconds between tries,
so a broken config can't spin the CPU. Only one supervisor per folder runs at a time (lock file).
"""
import datetime, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
LOG, CRASH, LOCK = (os.path.join(DATA, n) for n in ("server.log", "crash.log", "supervisor.lock"))
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def note(msg):
    with open(CRASH, "a", encoding="utf8") as f:
        f.write(f"{now()}  {msg}\n")


def pid_alive(pid):
    if sys.platform == "win32":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, creationflags=NO_WINDOW)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def take_lock():
    try:
        old = int(open(LOCK).read().strip())
        if old != os.getpid() and pid_alive(old):
            return False
    except (OSError, ValueError):
        pass
    open(LOCK, "w").write(str(os.getpid()))
    return True


def rotate():
    try:
        if os.path.getsize(LOG) > 5 * 1024 * 1024:
            os.replace(LOG, LOG + ".1")
    except OSError:
        pass


def tail(path, n=25):
    try:
        with open(path, encoding="utf8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


def main():
    os.makedirs(DATA, exist_ok=True)
    if not take_lock():
        return  # another supervisor already looks after this folder
    note(f"supervisor started (pid {os.getpid()}), args: {' '.join(sys.argv[1:])}")
    stops = []
    while True:
        rotate()
        start = time.time()
        with open(LOG, "a", encoding="utf8") as out:
            out.write(f"\n=== {now()} starting MoBetter ===\n")
            out.flush()
            p = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"), *sys.argv[1:]], cwd=HERE,
                                 stdout=out, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            code = p.wait()
        ran = int(time.time() - start)
        note(f"MoBetter stopped: exit code {code} after {ran // 3600}h {ran % 3600 // 60}m {ran % 60}s. Last output:\n"
             + "".join("    " + l for l in tail(LOG).splitlines(True)))
        stops = [t for t in stops if time.time() - t < 120] + [time.time()]
        wait = 60 if len(stops) >= 5 else 5
        if wait == 60:
            note("stopped 5 times within 2 minutes: waiting 60 s before the next try")
        time.sleep(wait)


if __name__ == "__main__":
    main()
