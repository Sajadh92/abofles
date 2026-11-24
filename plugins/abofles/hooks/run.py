"""Hook launcher: imports abofles_hook so Python caches its bytecode in hooks/__pycache__.
Running abofles_hook.py directly recompiles the whole module on every tool call (~13 ms each, more as it grows).
After a good import it keeps a last-good copy (~/.abofles/last-good/abofles_hook.py, same mtime): a live edit that does not
load (a syntax error) runs that copy instead, with one warning line on stderr, so the gates never all turn off at once."""
import os
import sys
import types

sys.dont_write_bytecode = False  # the hook environment sets PYTHONDONTWRITEBYTECODE=1; the cache is the point of this file
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "abofles_hook.py")
GOOD = os.path.join(os.environ.get("ABOFLES_LAST_GOOD") or os.path.join(os.path.expanduser("~"), ".abofles", "last-good"), "abofles_hook.py")
sys.path.insert(0, HERE)


def src_stamp():
    """(mtime_ns, size) of the live file, or None."""
    try:
        s = os.stat(SRC)
        return (s.st_mtime_ns, s.st_size)
    except OSError:
        return None


LOADED = src_stamp()  # the live file as this run imported it: only that file is ever kept


def keep_good():
    """Copy the module that just loaded to GOOD when it changed (one stat each when it did not). Never raises. A save that landed during the
    run (the file is no longer the one imported) or bytes that do not compile are never copied: a broken save never becomes last-good."""
    try:
        if os.path.exists(GOOD) and int(os.stat(GOOD).st_mtime) == int(os.stat(SRC).st_mtime) and os.path.getsize(GOOD) == os.path.getsize(SRC):
            return
        if LOADED is None or src_stamp() != LOADED:
            return
        with open(SRC, "rb") as f:
            data = f.read()
        if len(data) != LOADED[1] or src_stamp() != LOADED:
            return
        compile(data, SRC, "exec")  # a SyntaxError (a half-written save) raises: nothing is copied
        os.makedirs(os.path.dirname(GOOD), exist_ok=True)
        tmp = f"{GOOD}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.utime(tmp, ns=(LOADED[0], LOADED[0]))  # the mtime of the file imported: the next run's one-stat check
        os.replace(tmp, GOOD)
    except Exception:
        try:
            os.unlink(f"{GOOD}.{os.getpid()}.tmp")
        except OSError:
            pass


def load_good(err):
    """The last-good copy, run as abofles_hook from its own folder (paths next to it still resolve), or None when there is none."""
    try:
        with open(GOOD, encoding="utf-8") as f:
            code = compile(f.read(), SRC, "exec")
    except Exception:
        return None
    sys.stderr.write(f"abofles: hooks/abofles_hook.py does not load ({type(err).__name__}: {err}) — running the last good copy until it is fixed\n")
    mod = types.ModuleType("abofles_hook")
    mod.__file__ = SRC
    sys.modules["abofles_hook"] = mod
    exec(code, mod.__dict__)
    return mod


fresh = True
try:
    from abofles_hook import main  # noqa: E402
except Exception as e:  # a syntax error, a missing name or any other failure at import: every gate would be off
    sys.modules.pop("abofles_hook", None)
    good = load_good(e)
    if good is None:
        raise
    main = good.main
    fresh = False

def finish():
    """Keep the live file as the last good copy after a whole run. main() swallows a runtime error (the hook fails open) and records it in
    RUN_ERROR: that run is no whole run, so the copy is not replaced, and one warning line goes to stderr."""
    if not fresh:
        return
    err = getattr(sys.modules.get("abofles_hook"), "RUN_ERROR", None)
    if err:
        sys.stderr.write(f"abofles: hook error ({err}) — the last good copy was kept, not replaced\n")
        return
    keep_good()


# the live file becomes the last good copy only after a whole run: one that imports but fails inside main() is never kept.
# A SystemExit is a hook's normal end (deny, block), so it counts as a whole run — unless main() recorded a swallowed error (RUN_ERROR).
try:
    main()
except SystemExit:
    finish()
    raise
else:
    finish()
