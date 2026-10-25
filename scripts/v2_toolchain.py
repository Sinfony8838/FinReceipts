"""Download checksum-verified build tools into ignored workspace cache."""

import hashlib
import zipfile
from pathlib import Path

import httpx

root = Path(__file__).resolve().parents[1] / ".cache" / "v2-tools"
root.mkdir(parents=True, exist_ok=True)
with httpx.Client(follow_redirects=True, timeout=120) as client:
    assets = client.get(
        "https://api.adoptium.net/v3/assets/latest/21/hotspot",
        params={"architecture": "x64", "image_type": "jdk", "os": "windows"},
    )
    assets.raise_for_status()
    package = assets.json()[0]["binary"]["package"]
    downloads = [("jdk.zip", package["link"], package["checksum"], "sha256")]
    base = "https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.11/apache-maven-3.9.11-bin.zip"
    checksum = client.get(base + ".sha512")
    checksum.raise_for_status()
    downloads.append(("maven.zip", base, checksum.text.strip().split()[0], "sha512"))
    for name, url, expected, algorithm in downloads:
        path = root / name
        if not path.exists():
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with path.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
        if hashlib.new(algorithm, path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"checksum mismatch: {name}")
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if not (root / member).resolve().is_relative_to(root.resolve()):
                    raise RuntimeError("unsafe archive member")
            archive.extractall(root)
        print(name, "verified and extracted", flush=True)
