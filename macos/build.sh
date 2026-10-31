#!/bin/zsh
# Builds dist/LiveCap.dmg. Needs only the Xcode Command Line Tools and an internet connection.
# Downloads and the ffmpeg build are cached in build/, so later builds are quick.
#
# Free builds are signed ad hoc. To sign and notarize for distribution, set both of:
#   SIGN_ID="Developer ID Application: Your Name (TEAMID)"
#   NOTARY_PROFILE=<a profile saved with xcrun notarytool store-credentials>
set -euo pipefail
setopt null_glob

ROOT="${0:a:h:h}"
BUILD="$ROOT/build"
CACHE="$BUILD/cache"
APP="$BUILD/LiveCap.app"
RES="$APP/Contents/Resources"
MIN_MACOS=14.0
PYTHON_RELEASE=20261026
PYTHON_VERSION=3.13.18
FFMPEG_VERSION=7.1.1
SRT_VERSION=1.5.7
SIGN_ID="${SIGN_ID:--}"

mkdir -p "$CACHE" "$ROOT/dist"
cd "$BUILD"

fetch() { [[ -f "$CACHE/$2" ]] || curl -fsSL -o "$CACHE/$2" "$1"; }

fetch "https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_RELEASE/cpython-$PYTHON_VERSION%2B$PYTHON_RELEASE-aarch64-apple-darwin-install_only_stripped.tar.gz" python.tar.gz

build_ffmpeg() {
  local stage="$BUILD/ffmpeg-stage"
  [[ -x "$stage/bin/ffmpeg" ]] && return
  echo "Building ffmpeg (audio only)..."
  rm -rf "$BUILD/tools" "$BUILD/src" "$stage"
  mkdir -p "$BUILD/src" "$BUILD/tools"
  tar -xzf "$CACHE/python.tar.gz" -C "$BUILD/tools"
  "$BUILD/tools/python/bin/python3" -m pip install -q cmake pkgconf
  fetch "https://github.com/Haivision/srt/archive/refs/tags/v$SRT_VERSION.tar.gz" srt.tar.gz
  fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" ffmpeg.tar.xz
  tar -xzf "$CACHE/srt.tar.gz" -C "$BUILD/src"
  tar -xJf "$CACHE/ffmpeg.tar.xz" -C "$BUILD/src"
  local tools="$BUILD/tools/python/bin"
  (
    cd "$BUILD/src/srt-$SRT_VERSION"
    # Encryption is off so that no OpenSSL is needed. OBS needs no passphrase to reach LiveCap.
    "$tools/cmake" -S . -B out -DCMAKE_INSTALL_PREFIX="$stage" -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_OSX_DEPLOYMENT_TARGET=$MIN_MACOS -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
      -DENABLE_ENCRYPTION=OFF -DENABLE_SHARED=OFF -DENABLE_STATIC=ON -DENABLE_APPS=OFF >/dev/null
    "$tools/cmake" --build out --target install -j8 >/dev/null
  )
  (
    cd "$BUILD/src/ffmpeg-$FFMPEG_VERSION"
    PKG_CONFIG_PATH="$stage/lib/pkgconfig" ./configure --prefix="$stage" --pkg-config="$tools/pkgconf" \
      --pkg-config-flags=--static --extra-cflags="-mmacosx-version-min=$MIN_MACOS" \
      --extra-ldflags="-mmacosx-version-min=$MIN_MACOS" \
      --disable-everything --disable-autodetect --disable-doc --disable-programs --enable-ffmpeg --disable-debug \
      --enable-network --enable-libsrt --enable-protocol=rtmp,tcp,pipe,libsrt \
      --enable-demuxer=flv,live_flv,mpegts,aac,mp3,ogg \
      --enable-decoder=aac,aac_latm,mp3float,mp3,opus,ac3,eac3,flac,pcm_s16le \
      --enable-parser=aac,aac_latm,h264,hevc,av1,ac3,mpegaudio,opus \
      --enable-bsf=aac_adtstoasc,extract_extradata \
      --enable-muxer=pcm_f32le --enable-encoder=pcm_f32le \
      --enable-filter=aresample,aformat,anull,atrim,abuffer,abuffersink,null,buffer,buffersink >/dev/null
    make -j8 >/dev/null 2>&1
    make install >/dev/null
  )
}
build_ffmpeg

echo "Assembling the app..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES/bin"
tar -xzf "$CACHE/python.tar.gz" -C "$RES"
cp "$BUILD/ffmpeg-stage/bin/ffmpeg" "$RES/bin/ffmpeg"
cp -R "$ROOT/app" "$RES/app"
cp "$ROOT/macos/THIRD_PARTY.txt" "$RES/"
cp "$ROOT/macos/Info.plist" "$APP/Contents/Info.plist"

# Packages are chosen for the oldest supported macOS, not for the macOS doing the build.
SITE="$RES/python/lib/python3.13/site-packages"
"$RES/python/bin/python3" -m pip install -q --platform "macosx_14_0_arm64" --python-version 3.13 --only-binary=:all: \
  --target "$SITE" --upgrade -r "$ROOT/requirements-mac.txt" 2>&1 | grep -v "dependency resolver" || true

SUPERTONIC="$CACHE/supertonic-3"
if [[ ! -f "$SUPERTONIC/onnx/vocoder.onnx" ]]; then
  echo "Downloading the voice model..."
  HF_HOME="$CACHE/huggingface" PYTHONPATH="$SITE" "$RES/python/bin/python3" -c \
    "from supertonic import TTS; TTS(model='supertonic-3', model_dir='$SUPERTONIC')" >/dev/null
fi
cp -R "$SUPERTONIC" "$RES/supertonic-3"
rm -rf "$RES/supertonic-3/.cache"

# Drop what never runs, then precompile: the signed app must not change on disk.
# Package test folders stay, because some packages import from them at runtime.
find "$RES" -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$RES/python/lib/python3.13/test" "$RES/python/lib/python3.13/tkinter" "$RES/python/lib/python3.13/idlelib" "$RES/python/lib/python3.13/turtledemo" \
  "$RES/python/lib/python3.13/ensurepip" "$RES/python/lib/libtcl"* "$RES/python/lib/libtk"* "$RES/python/lib/tcl"* "$RES/python/lib/tk"* \
  "$RES/python/include" "$RES/python/share" "$SITE/pip" "$SITE/pip-"*
"$RES/python/bin/python3" -m compileall -q -j 8 "$RES/python/lib/python3.13" "$RES/app" >/dev/null 2>&1 || true

echo "Compiling the menu bar app..."
swiftc -O -swift-version 5 -parse-as-library -target arm64-apple-macos$MIN_MACOS \
  -framework AppKit -framework ServiceManagement "$ROOT/macos/LiveCap.swift" "$ROOT/macos/Updater.swift" -o "$APP/Contents/MacOS/LiveCap"

ICONSET="$BUILD/LiveCap.iconset"
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
swiftc -O -framework AppKit "$ROOT/macos/make_icon.swift" -o "$BUILD/make_icon"
for size in 16 32 128 256 512; do
  "$BUILD/make_icon" $size "$ICONSET/icon_${size}x${size}.png"
  "$BUILD/make_icon" $((size * 2)) "$ICONSET/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$ICONSET" -o "$RES/LiveCap.icns"

echo "Signing..."
OPTIONS=(--force --timestamp=none --sign "$SIGN_ID")
[[ "$SIGN_ID" != "-" ]] && OPTIONS=(--force --timestamp --options runtime --entitlements "$ROOT/macos/entitlements.plist" --sign "$SIGN_ID")
find "$RES" -type f \( -name '*.so' -o -name '*.dylib' -o -perm +111 \) -print0 | while IFS= read -r -d '' file; do
  if file -b "$file" | grep -q Mach-O; then codesign "${OPTIONS[@]}" "$file" 2>/dev/null; fi
done
codesign "${OPTIONS[@]}" "$APP"
codesign --verify --strict "$APP"

echo "Making the disk image..."
STAGE="$BUILD/dmg"
rm -rf "$STAGE" "$ROOT/dist/LiveCap.dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
# hdiutil can fail with a passing "resource busy", so give it a few tries.
for attempt in 1 2 3; do
  hdiutil create -volname LiveCap -srcfolder "$STAGE" -format ULFO "$ROOT/dist/LiveCap.dmg" 2>&1 | grep -v deprecated || true
  [[ -f "$ROOT/dist/LiveCap.dmg" ]] && break
  sleep 5
done
[[ -f "$ROOT/dist/LiveCap.dmg" ]] || { echo "Couldn't create the disk image." >&2; exit 1; }
rm -rf "$STAGE"

if [[ "$SIGN_ID" != "-" ]]; then
  codesign --force --timestamp --sign "$SIGN_ID" "$ROOT/dist/LiveCap.dmg"
  xcrun notarytool submit "$ROOT/dist/LiveCap.dmg" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$ROOT/dist/LiveCap.dmg"
fi
echo "App: $(du -sh "$APP" | cut -f1)   Disk image: $(du -sh "$ROOT/dist/LiveCap.dmg" | cut -f1)"
