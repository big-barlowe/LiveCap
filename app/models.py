import logging
import os
import shutil
import tempfile
import threading
import urllib.request
from pathlib import Path

log = logging.getLogger("livecap.models")

DATA_DIR = Path(os.environ.get("LIVECAP_DATA", "data"))

_locks = {}
_locks_guard = threading.Lock()


def lock_for(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def download(url, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s", url)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LiveCap/1.0"})
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=60) as response:
            shutil.copyfileobj(response, out, 1 << 20)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
