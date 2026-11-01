#!/usr/bin/env bash
# Build CachyBench-<version>-x86_64.AppImage (Python+PySide6+matplotlib + shell scripts)
# Run from the repository root. Requires a venv at build/venv (or CACHY_BENCH_VENV)
# with PySide6 and matplotlib installed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# Flat layout: scripts and app/ live at repo root (no nested src/).
SRC="${ROOT}"
VENV="${CACHY_BENCH_VENV:-${ROOT}/build/venv}"
DIST="${ROOT}/dist"
BUILD="${ROOT}/build"
APPDIR="${DIST}/CachyBench.AppDir"
VERSION="$(python3 -c "import sys; sys.path.insert(0, '${SRC}/app'); from version import __version__; print(__version__)")"
OUT="${DIST}/CachyBench-${VERSION}-x86_64.AppImage"
OUT_STABLE="${DIST}/CachyBench-x86_64.AppImage"
TOOL="${ROOT}/tools/appimagetool-x86_64.AppImage"
PYVER=3.13

if [ ! -x "${VENV}/bin/python" ]; then
  echo "Missing venv at ${VENV}. Create one with PySide6 + matplotlib, or set CACHY_BENCH_VENV." >&2
  exit 1
fi

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/share/cachy-bench/app" \
  "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
  "$DIST" "$BUILD" "$(dirname "$TOOL")"

echo "==> Bundle Python ${PYVER} runtime"
cp -a "/usr/bin/python${PYVER}" "$APPDIR/usr/bin/python3"
cp -a /usr/lib/x86_64-linux-gnu/libpython${PYVER}.so* "$APPDIR/usr/lib/" 2>/dev/null || \
  cp -a /usr/lib/libpython${PYVER}.so* "$APPDIR/usr/lib/"
cp -a "/usr/lib/python${PYVER}" "$APPDIR/usr/lib/"
rm -rf "$APPDIR/usr/lib/python${PYVER}/site-packages"
mkdir -p "$APPDIR/usr/lib/python${PYVER}/site-packages"
find "$APPDIR/usr/lib/python${PYVER}" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> Copy PySide6 Essentials + matplotlib stack"
SP="$VENV/lib/python${PYVER}/site-packages"
DEST="$APPDIR/usr/lib/python${PYVER}/site-packages"
for name in \
  PySide6 shiboken6 shiboken6_helpers.cpython-313-x86_64-linux-gnu.so \
  matplotlib mpl_toolkits pylab.py \
  numpy numpy.libs \
  PIL pillow.libs \
  contourpy cycler fontTools kiwisolver \
  packaging pyparsing dateutil \
  tzdata \
  ; do
  if [ -e "$SP/$name" ]; then
    cp -a "$SP/$name" "$DEST/"
  fi
done
shopt -s nullglob
for d in "$SP"/PySide6*.dist-info "$SP"/shiboken6*.dist-info \
         "$SP"/matplotlib*.dist-info "$SP"/numpy*.dist-info \
         "$SP"/pillow*.dist-info "$SP"/contourpy*.dist-info \
         "$SP"/cycler*.dist-info "$SP"/fonttools*.dist-info \
         "$SP"/kiwisolver*.dist-info "$SP"/packaging*.dist-info \
         "$SP"/pyparsing*.dist-info "$SP"/python_dateutil*.dist-info \
         "$SP"/tzdata*.dist-info; do
  cp -a "$d" "$DEST/"
done
shopt -u nullglob

echo "==> Strip host fontconfig/pango/glib/GL/X11/Wayland libs that clash with system"
find "$APPDIR" -type f \( \
  -name 'libGL.so*' -o -name 'libEGL.so*' -o -name 'libGLX.so*' -o \
  -name 'libGLdispatch.so*' -o -name 'libOpenGL.so*' -o \
  -name 'libnvidia*' -o -name 'libdrm.so*' -o -name 'libdrm_*' -o \
  -name 'libX11.so*' -o -name 'libX11-xcb.so*' -o -name 'libxcb.so*' -o -name 'libxcb-*.so*' -o \
  -name 'libXext.so*' -o -name 'libXrender.so*' -o -name 'libXi.so*' -o \
  -name 'libXrandr.so*' -o -name 'libXcursor.so*' -o -name 'libXinerama.so*' -o \
  -name 'libXxf86vm.so*' -o -name 'libXfixes.so*' -o \
  -name 'libwayland*.so*' -o -name 'libxkbcommon*.so*' -o \
  -name 'libEGL_mesa.so*' -o -name 'libGLX_mesa.so*' -o \
  -name 'libglapi.so*' -o -name 'libgbm.so*' -o \
  -name 'libfontconfig.so*' -o -name 'libfreetype.so*' -o \
  -name 'libpango-*.so*' -o -name 'libpangocairo-*.so*' -o -name 'libpangoft2-*.so*' -o \
  -name 'libharfbuzz.so*' -o -name 'libharfbuzz-*.so*' -o \
  -name 'libglib-*.so*' -o -name 'libgobject-*.so*' -o -name 'libgio-*.so*' -o \
  -name 'libgmodule-*.so*' -o -name 'libgthread-*.so*' -o \
  -name 'libgraphite2.so*' -o -name 'libfribidi.so*' -o -name 'libthai.so*' -o \
  -name 'libdatrie.so*' \
  \) ! -path '*/pillow.libs/*' -print -delete || true

echo "==> Ship benchmark scripts + LICENSE/version"
cp -a "$SRC/cachy-bench.sh" "$SRC/compare.sh" "$APPDIR/usr/share/cachy-bench/"
cp -a "$SRC/app/cachy-bench-gui.py" "$SRC/app/metrics.py" "$SRC/app/changes.py" "$SRC/app/version.py" \
  "$APPDIR/usr/share/cachy-bench/app/"
cp -a "$SRC/LICENSE" "$APPDIR/usr/share/cachy-bench/LICENSE"
cp -a "$SRC/LICENSE" "$APPDIR/LICENSE"
cp -a "$ROOT/CHANGELOG.md" "$APPDIR/usr/share/cachy-bench/CHANGELOG.md" 2>/dev/null || true
chmod +x "$APPDIR/usr/share/cachy-bench/cachy-bench.sh" \
         "$APPDIR/usr/share/cachy-bench/compare.sh" \
         "$APPDIR/usr/share/cachy-bench/app/cachy-bench-gui.py"

echo "==> Icon"
ICON_SRC="${ROOT}/cachy-bench.png"
if [ -f "$ICON_SRC" ]; then
  cp -a "$ICON_SRC" "$APPDIR/cachy-bench.png"
  cp -a "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/cachy-bench.png"
else
  "$VENV/bin/python" - "$APPDIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
try:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (256, 256), (20, 30, 48, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((32, 32, 224, 224), radius=32, fill=(30, 120, 80, 255))
    d.rectangle((70, 150, 100, 200), fill=(230, 230, 230, 255))
    d.rectangle((110, 110, 140, 200), fill=(230, 230, 230, 255))
    d.rectangle((150, 70, 180, 200), fill=(230, 230, 230, 255))
    img.save(root / "cachy-bench.png")
    img.save(root / "usr/share/icons/hicolor/256x256/apps/cachy-bench.png")
    print("icon generated")
except Exception as e:
    print("icon skip", e)
PY
fi

cat > "$APPDIR/cachy-bench.desktop" << DESK
[Desktop Entry]
Type=Application
Name=CachyOS Benchmark
GenericName=System Benchmark
Comment=Run, log, and compare CachyOS performance baselines
Exec=cachy-bench
Icon=cachy-bench
Categories=System;Utility;
Terminal=false
StartupNotify=true
StartupWMClass=CachyOS Benchmark
X-AppImage-Version=${VERSION}
DESK
cp "$APPDIR/cachy-bench.desktop" "$APPDIR/usr/share/applications/cachy-bench.desktop"

cat > "$APPDIR/AppRun" << 'APPRUN'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
export APPDIR="$HERE"
export CACHY_BENCH_APP="${CACHY_BENCH_APP:-$HERE/usr/share/cachy-bench}"
export CACHY_BENCH_DATA="${CACHY_BENCH_DATA:-$HOME/bench}"
mkdir -p "$CACHY_BENCH_DATA/runs" "$CACHY_BENCH_DATA/tmp"

# Prefer bundled interpreter + libs; keep HOST graphics/fontconfig via system paths.
export PYTHONHOME="$HERE/usr"
export PYTHONPATH="$HERE/usr/lib/python3.13/site-packages${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$HERE/usr/bin:$PATH"
PY_QT_LIB="$HERE/usr/lib/python3.13/site-packages/PySide6/Qt/lib"
SHIB_LIB="$HERE/usr/lib/python3.13/site-packages/shiboken6"
export LD_LIBRARY_PATH="$HERE/usr/lib${PY_QT_LIB:+:$PY_QT_LIB}${SHIB_LIB:+:$SHIB_LIB}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Avoid Qt picking up a broken plugin path from the host when mixed
export QT_PLUGIN_PATH="$HERE/usr/lib/python3.13/site-packages/PySide6/Qt/plugins"
export QML2_IMPORT_PATH="$HERE/usr/lib/python3.13/site-packages/PySide6/Qt/qml"

GUI="$CACHY_BENCH_APP/app/cachy-bench-gui.py"
SCRIPT="$CACHY_BENCH_APP/cachy-bench.sh"

if [[ "${1:-}" == "--run" ]]; then
  shift
  exec /usr/bin/env bash "$SCRIPT" "$@"
fi
if [[ "${1:-}" == "--compare" ]]; then
  shift
  exec /usr/bin/env bash "$CACHY_BENCH_APP/compare.sh" "$@"
fi

exec "$HERE/usr/bin/python3" "$GUI" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

ln -sf ../share/cachy-bench/app/cachy-bench-gui.py "$APPDIR/usr/bin/cachy-bench" 2>/dev/null || true

echo "==> Slim unused Qt Quick/QML/Designer"
"$VENV/bin/python" "$ROOT/slim_appdir.py" "$APPDIR"

echo "==> Pack AppImage"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$TOOL"
fi
export ARCH=x86_64
rm -f "$OUT"
set +e
APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$OUT" 2>"$BUILD/appimagetool.err"
rc=$?
set -e
if [ $rc -ne 0 ] || [ ! -f "$OUT" ]; then
  echo "appimagetool failed; trying extract fallback"
  mkdir -p "$BUILD/appimagetool"
  cd "$BUILD/appimagetool"
  APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --appimage-extract >/dev/null 2>&1 || true
  if [ -x squashfs-root/AppRun ]; then
    ARCH=x86_64 ./squashfs-root/AppRun "$APPDIR" "$OUT"
  else
    cat "$BUILD/appimagetool.err" || true
    exit 1
  fi
fi
chmod +x "$OUT"
rm -f "$OUT_STABLE"
cp -a "$OUT" "$OUT_STABLE"
ls -lh "$OUT" "$OUT_STABLE"
echo "DONE $OUT (also $OUT_STABLE)"
sha256sum "$OUT" "$OUT_STABLE"
