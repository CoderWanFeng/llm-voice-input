#!/bin/bash
# 把 Swift Package 构建产物打包成 .app bundle
# 这样系统才能正确授予麦克风 / 辅助功能 / Input Monitoring 权限

set -e

APP_NAME="VoiceInput"
BUNDLE_ID="com.voiceinput.app"
BUILD_DIR="build"
APP_BUNDLE="$BUILD_DIR/$APP_NAME.app"

echo "==> 1. Release 构建"
swift build -c release

echo "==> 2. 创建 .app bundle 结构"
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

# 拷贝可执行文件
cp ".build/release/$APP_NAME" "$APP_BUNDLE/Contents/MacOS/$APP_NAME"

# 拷贝 Info.plist
cp "Resources/Info.plist" "$APP_BUNDLE/Contents/Info.plist"

# 拷贝 SPM 资源（当前项目暂无资源 bundle，保留兼容）
if [ -d ".build/release/$APP_NAME_VoiceInput.bundle" ]; then
    cp -R ".build/release/$APP_NAME_VoiceInput.bundle" "$APP_BUNDLE/Contents/Resources/"
fi

# 签名（ad-hoc，足够本地运行）
echo "==> 3. ad-hoc 签名"
codesign --force --deep --sign - "$APP_BUNDLE"

echo ""
echo "✅ 完成: $APP_BUNDLE"
echo ""
echo "运行: open $APP_BUNDLE"
echo "或直接: $APP_BUNDLE/Contents/MacOS/$APP_NAME"
