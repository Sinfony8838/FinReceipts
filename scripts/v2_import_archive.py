"""Validate a legacy JSON export, then upload its original bytes as a read-only archive."""

import argparse
import hashlib
import json
import os
from pathlib import Path

import httpx

from finreceipts.runtime.store import verify_export

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("export", type=Path)
parser.add_argument("--url", default="http://localhost:8090")
args = parser.parse_args()
raw = args.export.read_bytes()
verify_export(json.loads(raw))
response = httpx.post(
    args.url + "/api/v2/archives",
    content=raw,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ["FINRECEIPTS_V2_TOKEN"],
    },
    timeout=30,
)
response.raise_for_status()
assert response.json()["sha256"] == hashlib.sha256(raw).hexdigest()
print(json.dumps(response.json(), ensure_ascii=False))
