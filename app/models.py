"""Whisper model management: which models are available offline, background download, base-model fallback."""
import importlib.util, socket, threading, time

SIZES = {"tiny": "~75 MB", "base": "~145 MB", "small": "~480 MB", "medium": "~1.5 GB", "large-v3": "~3 GB"}
_dl, _lock = {}, threading.Lock()
_cache = {}  # name -> (time, bool)
_net = [0.0, False]


def available():
    return importlib.util.find_spec("faster_whisper") is not None


def is_downloaded(name, fresh=False):
    """True if the model files are already on this computer (usable offline)."""
    if not available():
        return False
    t, v = _cache.get(name, (0, False))
    if not fresh and time.time() - t < 5:
        return v
    try:
        from faster_whisper.utils import download_model
        download_model(name, local_files_only=True)
        v = True
    except Exception:
        v = False
    _cache[name] = (time.time(), v)
    return v


def effective(name):
    """The model that will really be used: the chosen one if it is downloaded, otherwise 'base'."""
    return name if (name == "base" or is_downloaded(name)) else "base"


def start_download(name):
    if name not in SIZES:
        raise ValueError(f"Unknown model '{name}'")
    if not available():
        raise ValueError("faster-whisper is not installed (run setup.bat)")
    with _lock:
        if _dl.get(name, {}).get("state") == "downloading":
            return
        _dl[name] = dict(state="downloading", error="", started=time.time())

    def run():
        try:
            from faster_whisper.utils import download_model
            download_model(name)
            _dl[name] = dict(state="done", error="")
            _cache.pop(name, None)
        except Exception as e:
            _dl[name] = dict(state="error", error=str(e)[:300])
    threading.Thread(target=run, daemon=True, name=f"download-{name}").start()


def status(selected):
    out = []
    for n, size in SIZES.items():
        d = _dl.get(n, {})
        out.append(dict(name=n, size=size, downloaded=is_downloaded(n), state=d.get("state", ""), error=d.get("error", ""),
                        selected=n == selected))
    return out


def online(timeout=2.0):
    """Cheap internet check (cached 30 s): can we open a TCP connection to the model host?"""
    if time.time() - _net[0] < 30:
        return _net[1]
    ok = False
    for host in ("huggingface.co", "1.1.1.1"):
        try:
            socket.create_connection((host, 443), timeout=timeout).close()
            ok = True
            break
        except OSError:
            continue
    _net[0], _net[1] = time.time(), ok
    return ok
