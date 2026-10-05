import os
import shutil
import zipfile
import hashlib
from pathlib import Path

root = Path(__file__).resolve().parent
release_dir = root / "release_assets"
if release_dir.exists():
    shutil.rmtree(release_dir)
release_dir.mkdir(parents=True, exist_ok=True)

# 1. Copy Standalone Windows x64 EXE
exe_src = root / "dist" / "Sadh.exe"
if exe_src.exists():
    exe_dst = release_dir / "Sadh-windows-x64.exe"
    shutil.copy2(exe_src, exe_dst)
    print(f"[Release] Copied {exe_dst.name} ({exe_dst.stat().st_size / (1024*1024):.2f} MB)")

# 2. Copy Python wheel and sdist
release_dist = root / "release_dist"
for f in release_dist.glob("*"):
    dst = release_dir / f.name
    shutil.copy2(f, dst)
    print(f"[Release] Copied {dst.name}")

# 3. Create portable ZIP
zip_path = release_dir / "Sadh-v1.0.0-windows-x64.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(exe_src, arcname="Sadh.exe")
    z.write(root / "README.md", arcname="README.md")
    z.write(root / "LICENSE", arcname="LICENSE")
    z.write(root / "config.json.example", arcname="config.json.example")
    z.write(root / "install.ps1", arcname="install.ps1")
    for asset in (root / "assets").glob("*"):
        z.write(asset, arcname=f"assets/{asset.name}")
print(f"[Release] Created portable zip {zip_path.name} ({zip_path.stat().st_size / (1024*1024):.2f} MB)")

# 4. Generate SHA256SUMS.txt
checksums = []
for item in sorted(release_dir.glob("*")):
    if item.is_file():
        h = hashlib.sha256(item.read_bytes()).hexdigest()
        checksums.append(f"{h}  {item.name}")

(release_dir / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
print("[Release] Generated SHA256SUMS.txt:")
for line in checksums:
    print(" ", line)
