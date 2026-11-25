# PyInstaller spec for the ImageVault sidecar (one-folder build).
# Build:  uv run pyinstaller --noconfirm --distpath dist --workpath build packaging/ivsidecar.spec
# Output: dist/ivsidecar/ivsidecar(.exe) + dist/ivsidecar/_internal/
# Optional: put exiftool at dist/ivsidecar/exiftool/exiftool.exe (CI downloads it).
import glob
import os
import sysconfig

from PyInstaller.utils.hooks import collect_all, collect_data_files

site = sysconfig.get_paths()["purelib"]

datas = collect_data_files("ivsidecar", includes=["migrations/*.sql"])
binaries = []
hiddenimports = ["_libvips", "ivsidecar.migrations"]

# pyvips-binary ships the native _libvips extension plus a vendored libs folder
# (pyvips_binary.libs) that PyInstaller does not discover on its own.
for path in glob.glob(os.path.join(site, "pyvips_binary.libs", "*")):
    binaries.append((path, "pyvips_binary.libs"))
for path in glob.glob(os.path.join(site, "_libvips*")):
    binaries.append((path, "."))

for pkg in ("sqlite_vec", "pyvips"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ivsidecar", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ivsidecar", upx=False)
