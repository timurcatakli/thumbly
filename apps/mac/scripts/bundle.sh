#!/bin/bash
# Builds Thumbly.app into build/. Pass --install to also copy it to ~/Applications.
set -euo pipefail
cd "$(dirname "$0")/.."

swift build -c release
BIN="$(swift build -c release --show-bin-path)/Thumbly"

APP=build/Thumbly.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Thumbly"
cp Resources/SchibstedGrotesk.ttf "$APP/Contents/Resources/"
cp Resources/OFL.txt "$APP/Contents/Resources/SchibstedGrotesk-OFL.txt"
cp -R Resources/ClientIcons "$APP/Contents/Resources/"

if [ ! -f build/AppIcon.icns ] || [ scripts/make_icon.swift -nt build/AppIcon.icns ]; then
  swift scripts/make_icon.swift build
fi
cp build/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

VERSION="${THUMBLY_APP_VERSION:-0.1.0}"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Thumbly</string>
  <key>CFBundleDisplayName</key><string>Thumbly</string>
  <key>CFBundleIdentifier</key><string>com.thumbly.mac</string>
  <key>CFBundleExecutable</key><string>Thumbly</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><false/>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Thumbly. Schibsted Grotesk font under SIL OFL 1.1.</string>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleURLName</key><string>com.thumbly.mac</string>
      <key>CFBundleURLSchemes</key><array><string>thumbly</string></array>
    </dict>
  </array>
</dict>
</plist>
PLIST

codesign --force -s - --deep "$APP"
echo "Built $APP"

if [ "${1:-}" = "--install" ]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/Thumbly.app"
  cp -R "$APP" "$HOME/Applications/"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$HOME/Applications/Thumbly.app"
  echo "Installed to ~/Applications/Thumbly.app"
fi
