# PyInstaller spec for the ImageVault sidecar (one-folder build).
# Build:  uv run pyinstaller --noconfirm --distpath dist --workpath build packaging/ivsidecar.spec
# Output: dist/ivsidecar/ivsidecar(.exe) + dist/ivsidecar/_internal/
# Optional: put exiftool at dist/ivsidecar/exiftool/exiftool.exe (CI downloads it).
import glob
import os
import sysconfig

from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

site = sysconfig.get_paths()["purelib"]

datas = collect_data_files("ivsidecar", includes=["migrations/*.sql"])
binaries = []
hiddenimports = ["_libvips", "ivsidecar.migrations", "watchdog.observers.read_directory_changes", "watchdog.observers.winapi"]

# pyvips-binary ships the native _libvips extension plus a vendored libs folder
# (pyvips_binary.libs) that PyInstaller does not discover on its own.
for path in glob.glob(os.path.join(site, "pyvips_binary.libs", "*")):
    binaries.append((path, "pyvips_binary.libs"))
for path in glob.glob(os.path.join(site, "_libvips*")):
    binaries.append((path, "."))
# pillow-heif wheels vendor libheif/libde265/x265 the same way (pillow_heif.libs on Linux,
# delvewheel-style pillow_heif.libs on Windows).
for path in glob.glob(os.path.join(site, "pillow_heif.libs", "*")):
    binaries.append((path, "pillow_heif.libs"))

# uvicorn picks its protocol layers by import string at run time (WebSockets for /v1/events)
hiddenimports += collect_submodules("uvicorn") + collect_submodules("websockets")

for pkg in ("sqlite_vec", "pyvips", "pillow_heif"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ivsidecar", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ivsidecar", upx=False)
