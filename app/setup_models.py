"""Downloads the speech model on first launch, printing progress for the menu bar app to show."""
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

REVISION = "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15"
BASE = f"https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3/resolve/{REVISION}"
MODEL_BYTES = 2508288736
MODEL_SHA256 = "05e01c7f396c298cf7d23f61da7b504adeab698f0aaeafd9c82d198625464592"
CHUNK = 1 << 20


def fetch(name, target, expected=None):
    final = target / name
    if final.exists():
        return
    partial = final.with_name(name + ".part")
    have = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": "LiveCap/1.0"})
    if have:
        request.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(request, timeout=60) as response:
        # A server that ignores the range sends the whole file again.
        mode = "ab" if response.status == 206 else "wb"
        have = have if response.status == 206 else 0
        with open(partial, mode) as out:
            while chunk := response.read(CHUNK):
                out.write(chunk)
                have += len(chunk)
                if expected:
                    print(f"PROGRESS {have} {expected}", flush=True)
    os.replace(partial, final)


def verify(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest() == MODEL_SHA256


def main():
    target = Path(os.environ["LIVECAP_PARAKEET_DIR"])
    target.mkdir(parents=True, exist_ok=True)
    try:
        fetch("config.json", target)
        fetch("model.safetensors", target, MODEL_BYTES)
        print("VERIFYING", flush=True)
        if not verify(target / "model.safetensors"):
            (target / "model.safetensors").unlink()
            raise ValueError("the download was damaged and has been discarded")
    except Exception as error:
        print(f"ERROR {error}", flush=True)
        sys.exit(1)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
