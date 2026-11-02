#!/usr/bin/env bash
# ==============================================================================
# CloudflareSpeedTest DDNS 自动化部署与安装脚本 (Debian / Ubuntu / Linux)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=========================================================="
echo " CloudflareSpeedTest DDNS 部署助手"
echo " 根目录: ${ROOT_DIR}"
echo "=========================================================="

# 1. 检查 Python 3 环境
if ! command -v python3 &> /dev/null; then
    echo "[!] 未检测到 python3，正在尝试安装..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y python3 curl tar
    else
        echo "[错误] 请先手动安装 Python 3 环境！"
        exit 1
    fi
fi

echo "[✓] Python 版本: $(python3 --version)"

# 2. 检查或准备 CloudflareSpeedTest 可执行文件
CFST_BIN="${ROOT_DIR}/cfst"
if [ -f "${CFST_BIN}" ] || [ -f "${ROOT_DIR}/CloudflareSpeedTest" ]; then
    echo "[✓] 检测到本地已存在 cfst 测速程序，跳过准备步骤"
elif command -v go &> /dev/null && [ -f "${ROOT_DIR}/main.go" ]; then
    echo "[*] 检测到本地 Go 环境，直接从当前仓库源码本地编译 cfst..."
    (cd "${ROOT_DIR}" && go build -ldflags "-s -w" -o cfst main.go)
    echo "[✓] 本地编译完成: ${ROOT_DIR}/cfst"
else
    echo "[*] 本地未找到 cfst 可执行文件且无 Go 环境，检测架构并下载官方预编译版本..."
    ARCH="$(uname -m)"
    case "${ARCH}" in
        x86_64|amd64)
            PKG_ARCH="linux_amd64"
            ;;
        aarch64|arm64)
            PKG_ARCH="linux_arm64"
            ;;
        i386|i686)
            PKG_ARCH="linux_386"
            ;;
        armv7*|armhf)
            PKG_ARCH="linux_armv7"
            ;;
        *)
            echo "[错误] 未识别的架构: ${ARCH}，请手动下载对应的二进制文件并重命名为 cfst 放置于项目根目录"
            exit 1
            ;;
    esac

    # 从 config.json (或 config.json.example) 读取 GitHub 加速代理，也可由 GH_PROXY 环境变量覆盖
    CONFIG_FILE="${SCRIPT_DIR}/config.json"
    CONFIG_EXAMPLE="${SCRIPT_DIR}/config.json.example"

    GH_PROXY="${GH_PROXY:-${GITHUB_PROXY:-}}"
    if [ -z "${GH_PROXY}" ] && [ -f "${CONFIG_FILE}" ]; then
        GH_PROXY=$(python3 -c "import json; cfg=json.load(open('${CONFIG_FILE}')); print(cfg.get('general', {}).get('github_proxy', ''))" 2>/dev/null || echo "")
    fi
    if [ -z "${GH_PROXY}" ] && [ -f "${CONFIG_EXAMPLE}" ]; then
        GH_PROXY=$(python3 -c "import json; cfg=json.load(open('${CONFIG_EXAMPLE}')); print(cfg.get('general', {}).get('github_proxy', ''))" 2>/dev/null || echo "")
    fi
    if [ -z "${GH_PROXY}" ]; then
        GH_PROXY="https://gh-proxy.org/"
    fi

    # 规范化代理前缀 (带尾随斜杠；若用户显式填 "" 或 "direct" 则直连)
    if [ -n "${GH_PROXY}" ] && [ "${GH_PROXY}" != "direct" ] && [ "${GH_PROXY}" != "none" ]; then
        GH_PROXY="${GH_PROXY%/}/"
    else
        GH_PROXY=""
    fi

    DOWNLOAD_URL="https://github.com/XIU2/CloudflareSpeedTest/releases/latest/download/cfst_${PKG_ARCH}.tar.gz"
    PROXIED_URL="${GH_PROXY}${DOWNLOAD_URL}"
    TMP_TGZ="/tmp/cfst_${PKG_ARCH}.tar.gz"

    echo "[*] 正在下载官方预编译包: ${PROXIED_URL}"
    if ! curl -fsSL --connect-timeout 15 -o "${TMP_TGZ}" "${PROXIED_URL}"; then
        echo "[!] 代理下载失败，尝试直连 GitHub: ${DOWNLOAD_URL}"
        if ! curl -fsSL --connect-timeout 10 -o "${TMP_TGZ}" "${DOWNLOAD_URL}"; then
            echo "[错误] 下载失败！请手动下载 ${DOWNLOAD_URL}，将其中的 cfst 文件放置于 ${ROOT_DIR}/ 目录下。"
            exit 1
        fi
    fi

    echo "[*] 正在解压至 ${ROOT_DIR} ..."
    tar -zxvf "${TMP_TGZ}" -C "${ROOT_DIR}" --strip-components=1 2>/dev/null || tar -zxvf "${TMP_TGZ}" -C "${ROOT_DIR}"
    if [ -d "${ROOT_DIR}/cfst_${PKG_ARCH}" ] && [ -f "${ROOT_DIR}/cfst_${PKG_ARCH}/cfst" ]; then
        mv -f "${ROOT_DIR}/cfst_${PKG_ARCH}/cfst" "${ROOT_DIR}/cfst" 2>/dev/null || true
        rm -rf "${ROOT_DIR}/cfst_${PKG_ARCH}"
    fi
    rm -f "${TMP_TGZ}"
    echo "[✓] CloudflareSpeedTest 二进制就绪"
fi

if [ -f "${ROOT_DIR}/cfst" ]; then
    chmod +x "${ROOT_DIR}/cfst"
elif [ -f "${ROOT_DIR}/CloudflareSpeedTest" ]; then
    chmod +x "${ROOT_DIR}/CloudflareSpeedTest"
fi

chmod +x "${SCRIPT_DIR}/main.py"

# 3. 检查配置文件
CONFIG_FILE="${SCRIPT_DIR}/config.json"
CONFIG_EXAMPLE="${SCRIPT_DIR}/config.json.example"

if [ ! -f "${CONFIG_FILE}" ]; then
    echo "[*] 创建配置文件 ${CONFIG_FILE} ..."
    cp "${CONFIG_EXAMPLE}" "${CONFIG_FILE}"
    echo "----------------------------------------------------------"
    echo "【重要提示】请使用文本编辑器填写您的 Cloudflare 配置:"
    echo "  nano ${CONFIG_FILE}"
    echo "  重点配置项: api_token, zone_name (example.com), record_names (*.cf.example.com, cf.example.com)"
    echo "----------------------------------------------------------"
fi

# 4. 配置或卸载 Systemd 定时任务
if [ "$1" = "--uninstall-systemd" ] || [ "$1" = "--uninstall" ] || [ "$1" = "-u" ]; then
    if [ "$(id -u)" -ne 0 ]; then
        echo "[错误] 卸载 systemd 服务需要 root 权限，请使用 sudo $0 --uninstall-systemd"
        exit 1
    fi

    echo "[*] 正在停止并卸载 systemd 服务与定时器..."
    systemctl stop cfst-ddns.timer 2>/dev/null || true
    systemctl disable cfst-ddns.timer 2>/dev/null || true
    systemctl stop cfst-ddns.service 2>/dev/null || true
    systemctl disable cfst-ddns.service 2>/dev/null || true

    rm -f /etc/systemd/system/cfst-ddns.service
    rm -f /etc/systemd/system/cfst-ddns.timer

    systemctl daemon-reload
    systemctl reset-failed 2>/dev/null || true

    echo "[✓] systemd 定时任务与服务已彻底卸载并清理完成！"
    exit 0
elif [ "$1" = "--install-systemd" ] || [ "$1" = "-s" ]; then
    if [ "$(id -u)" -ne 0 ]; then
        echo "[错误] 安装 systemd 服务需要 root 权限，请使用 sudo $0 --install-systemd"
        exit 1
    fi

    echo "[*] 正在配置 systemd 服务与定时器..."
    SERVICE_FILE="/etc/systemd/system/cfst-ddns.service"
    TIMER_FILE="/etc/systemd/system/cfst-ddns.timer"

    cat <<EOF > "${SERVICE_FILE}"
[Unit]
Description=CloudflareSpeedTest Dynamic DNS Updater
Documentation=https://github.com/XIU2/CloudflareSpeedTest
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=${ROOT_DIR}
ExecStart=$(command -v python3) ${SCRIPT_DIR}/main.py
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # 读取 config.json 中的运行间隔设置 (若未配置默认 6 小时)
    INTERVAL_HOURS=6
    RANDOM_MIN=0
    if [ -f "${CONFIG_FILE}" ]; then
        INTERVAL_HOURS=$(python3 -c "import json; cfg=json.load(open('${CONFIG_FILE}')); print(cfg.get('schedule', {}).get('interval_hours', 6))" 2>/dev/null || echo 6)
        RANDOM_MIN=$(python3 -c "import json; cfg=json.load(open('${CONFIG_FILE}')); print(cfg.get('schedule', {}).get('random_delay_minutes', 0))" 2>/dev/null || echo 0)
    fi

    echo "[*] 定时任务运行周期: 每 ${INTERVAL_HOURS} 小时"

    cat <<EOF > "${TIMER_FILE}"
[Unit]
Description=Run CloudflareSpeedTest DDNS update every ${INTERVAL_HOURS} hours
Documentation=https://github.com/XIU2/CloudflareSpeedTest

[Timer]
# 开机 2 分钟后启动首次测速
OnBootSec=2min
# 运行周期 (从 config.json schedule.interval_hours 读取)
OnUnitActiveSec=${INTERVAL_HOURS}h
Persistent=true
EOF

    if [ "${RANDOM_MIN}" -gt 0 ] 2>/dev/null; then
        echo "RandomizedDelaySec=${RANDOM_MIN}m" >> "${TIMER_FILE}"
    fi

    echo "" >> "${TIMER_FILE}"
    echo "[Install]" >> "${TIMER_FILE}"
    echo "WantedBy=timers.target" >> "${TIMER_FILE}"

    systemctl daemon-reload
    systemctl enable cfst-ddns.timer
    systemctl restart cfst-ddns.timer

    echo "[✓] systemd 定时任务安装成功！"
    echo "  - 查看定时器状态: systemctl list-timers --all | grep cfst"
    echo "  - 查看服务执行日志: journalctl -u cfst-ddns.service -f"
    echo "  - 手动触发一次更新: systemctl start cfst-ddns.service"
else
    echo ""
    echo "=========================================================="
    echo "下一步操作指引:"
    echo "1. 编辑配置文件并填入 Cloudflare API Token:"
    echo "   nano ${CONFIG_FILE}"
    echo "2. 校验配置连通性与权限:"
    echo "   python3 ${SCRIPT_DIR}/main.py --verify-only"
    echo "3. 试运行 (测速并模拟 DNS 变更，不实际修改):"
    echo "   python3 ${SCRIPT_DIR}/main.py --dry-run"
    echo "4. 一键安装 systemd 6 小时自动运行定时器:"
    echo "   sudo bash ${SCRIPT_DIR}/deploy.sh --install-systemd"
    echo "5. 卸载 systemd 定时器与服务:"
    echo "   sudo bash ${SCRIPT_DIR}/deploy.sh --uninstall-systemd"
    echo "=========================================================="
fi
