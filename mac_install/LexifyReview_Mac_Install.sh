#!/bin/bash
# ============================================================
#  LexifyReview - Mac Installer
#  Chạy file này 1 lần để cài đặt LexifyReview trên Mac
#  Usage: bash LexifyReview_Mac_Install.sh
# ============================================================

set -e

INSTALL_DIR="$HOME/LexifyReview"
VENV_DIR="$INSTALL_DIR/venv"
APP_PATH="/Applications/LexifyReview.app"
PYTHON_MIN="3.9"

echo "======================================"
echo "  LexifyReview - Mac Installer"
echo "======================================"
echo ""

# ── 1. Tìm Python ──
find_python() {
    for cmd in python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
        if command -v "$cmd" &>/dev/null; then
            VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
            MAJOR=$(echo "$VER" | cut -d. -f1)
            MINOR=$(echo "$VER" | cut -d. -f2)
            if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 9 ]; then
                echo "$cmd"
                return
            fi
        fi
    done
    echo ""
}

PYTHON=$(find_python)

if [ -z "$PYTHON" ]; then
    echo "❌ Không tìm thấy Python 3.9+."
    echo ""
    echo "Hãy cài Python từ: https://www.python.org/downloads/"
    echo "Sau đó chạy lại script này."
    echo ""
    read -p "Nhấn Enter để thoát..."
    exit 1
fi

PYTHON_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
echo "✅ Tìm thấy Python $PYTHON_VER ($PYTHON)"
echo ""

# ── 2. Tạo thư mục cài đặt ──
echo "📁 Tạo thư mục: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

# ── 3. Copy file app ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/shopify_reviews_gui.py" ]; then
    cp "$SCRIPT_DIR/shopify_reviews_gui.py" "$INSTALL_DIR/"
    echo "✅ Đã copy shopify_reviews_gui.py"
else
    echo "❌ Không tìm thấy shopify_reviews_gui.py bên cạnh file installer!"
    echo "   Đảm bảo 2 file cùng thư mục."
    read -p "Nhấn Enter để thoát..."
    exit 1
fi

# ── 4. Tạo virtual environment ──
echo ""
echo "🐍 Tạo Python virtual environment..."
"$PYTHON" -m venv "$VENV_DIR"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
echo "✅ Xong"

# ── 5. Cài thư viện ──
echo ""
echo "📦 Cài đặt thư viện (playwright, playwright-stealth, openpyxl)..."
"$VENV_PIP" install --upgrade pip --quiet
"$VENV_PIP" install playwright playwright-stealth openpyxl --quiet
echo "✅ Xong"

# ── 6. Cài Chromium ──
echo ""
echo "🌐 Tải Chromium (có thể mất vài phút lần đầu)..."
"$VENV_DIR/bin/playwright" install chromium
echo "✅ Xong"

# ── 7. Tạo script chạy ──
LAUNCHER="$INSTALL_DIR/run_lexifyreview.sh"
cat > "$LAUNCHER" << RUNEOF
#!/bin/bash
# Launcher cho LexifyReview
cd "$INSTALL_DIR"
exec caffeinate -dims "$VENV_PYTHON" "$INSTALL_DIR/shopify_reviews_gui.py" &> /tmp/lexifyreview.log
RUNEOF
chmod +x "$LAUNCHER"

# ── 8. Tạo .app bằng osacompile ──
echo ""
echo "🍎 Tạo LexifyReview.app trong Applications..."

APPLE_SCRIPT_CONTENT="do shell script \"caffeinate -dims '$VENV_PYTHON' '$INSTALL_DIR/shopify_reviews_gui.py' &> /tmp/lexifyreview.log &\""

# Xoá app cũ nếu có
if [ -d "$APP_PATH" ]; then
    rm -rf "$APP_PATH"
fi

# Tạo app mới
osacompile -o "$APP_PATH" -e "$APPLE_SCRIPT_CONTENT"

# Set icon nếu có (skip nếu không có)
# cp "$SCRIPT_DIR/icon.icns" "$APP_PATH/Contents/Resources/applet.icns" 2>/dev/null || true

echo "✅ Đã tạo LexifyReview.app"

# ── 9. Ghi launcher script để cập nhật app về sau ──
UPDATE_SCRIPT="$INSTALL_DIR/update.sh"
cat > "$UPDATE_SCRIPT" << UPDATEEOF
#!/bin/bash
echo "Đang cập nhật LexifyReview..."
"$VENV_PIP" install --upgrade playwright playwright-stealth openpyxl --quiet
echo "✅ Cập nhật xong!"
UPDATEEOF
chmod +x "$UPDATE_SCRIPT"

# ── Done ──
echo ""
echo "======================================"
echo "✅ CÀI ĐẶT HOÀN TẤT!"
echo "======================================"
echo ""
echo "  📌 App đã được cài vào: /Applications/LexifyReview.app"
echo "  📌 Mở Launchpad hoặc Applications để chạy"
echo ""
echo "  📌 Nếu Mac hỏi 'Can't be opened' khi mở app lần đầu:"
echo "     → Vào System Settings > Privacy & Security > Mở anyway"
echo "     Hoặc: Right-click vào app > Open > Open"
echo ""

# Hỏi có muốn mở app ngay không
read -p "Mở LexifyReview ngay bây giờ? (y/n): " OPEN_NOW
if [[ "$OPEN_NOW" == "y" || "$OPEN_NOW" == "Y" ]]; then
    open "$APP_PATH"
fi
