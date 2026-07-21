from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    if mode == "invalid-json":
        print("{not json")
        return 0
    if mode == "invalid-utf8":
        sys.stdout.buffer.write(b"\xff")
        return 0
    if mode == "exit":
        print("private memory text must not leak", file=sys.stderr)
        return 3
    if mode == "large-stdout":
        chunk = b"x" * 65_536
        for _ in range(1_600):
            os.write(sys.stdout.fileno(), chunk)
        return 0
    if mode == "infinite-stdout":
        chunk = b"x" * 65_536
        while True:
            os.write(sys.stdout.fileno(), chunk)
    if mode == "infinite-stderr":
        chunk = b"x" * 65_536
        while True:
            os.write(sys.stderr.fileno(), chunk)
    if mode == "spawn-pipe-holder":
        json.loads(sys.stdin.read() or "{}")
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=sys.stdout,
            stderr=sys.stderr,
            close_fds=False,
        )
        print(json.dumps({"vectors": [[1.0, 0.0, 0.0, 0.0]], "model": "fixture-embedding-v1"}))
        return 0

    payload = json.loads(sys.stdin.read() or "{}")
    dim = int(payload.get("dim") or 4)
    texts = payload.get("texts") or []
    vectors = [_vector_for(str(text), dim=dim) for text in texts]
    print(json.dumps({"vectors": vectors, "model": "fixture-embedding-v1"}))
    return 0


def _vector_for(text: str, *, dim: int) -> list[float]:
    vector = [0.0] * dim
    buckets = {
        "alpha": 0,
        "beta": 1,
        "gamma": 2,
        "delta": 3,
    }
    lowered = text.lower()
    for token, bucket in buckets.items():
        if token in lowered:
            vector[bucket % dim] += 1.0
    if not any(vector):
        vector[-1] = 1.0
    return vector


if __name__ == "__main__":
    raise SystemExit(main())
