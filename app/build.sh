#!/usr/bin/env bash
# Builds build/SampleBuddy.app (ad-hoc signed). Usage: ./build.sh [--install]  (--install copies to /Applications)
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release
APP=build/SampleBuddy.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$(swift build -c release --show-bin-path)/SampleBuddy" "$APP/Contents/MacOS/SampleBuddy"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' ../pyproject.toml)
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>SampleBuddy</string>
  <key>CFBundleDisplayName</key><string>SampleBuddy</string>
  <key>CFBundleIdentifier</key><string>app.samplebuddy.SampleBuddy</string>
  <key>CFBundleExecutable</key><string>SampleBuddy</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.music</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeName</key><string>Audio</string>
      <key>CFBundleTypeRole</key><string>Viewer</string>
      <key>LSHandlerRank</key><string>Alternate</string>
      <key>LSItemContentTypes</key><array><string>public.audio</string><string>public.folder</string></array>
    </dict>
  </array>
</dict>
</plist>
PLIST
codesign --force --sign - "$APP" >/dev/null
echo "built $APP"
if [ "${1:-}" = "--install" ]; then
  rm -rf /Applications/SampleBuddy.app && cp -R "$APP" /Applications/ && echo "installed /Applications/SampleBuddy.app"
fi
