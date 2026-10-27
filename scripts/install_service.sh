#!/usr/bin/env bash
# 安装为 systemd 用户服务：登录后自动在后台运行，按设定时间每天抓取。
# 卸载：systemctl --user disable --now roboradar && rm ~/.config/systemd/user/roboradar.service
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROBORADAR_PYTHON:-$HOME/miniconda3/envs/roboradar/bin/python}"
PORT="${1:-8770}"
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/roboradar.service <<UNIT
[Unit]
Description=RoboRadar 机器人论文与动态雷达
After=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$PY -m roboradar serve --port $PORT
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now roboradar
echo "已启动：http://127.0.0.1:$PORT"
echo "查看日志：journalctl --user -u roboradar -f"

# 应用菜单快捷方式（点开即在浏览器打开界面）
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/roboradar.desktop <<DESK
[Desktop Entry]
Type=Application
Name=RoboRadar 机器人雷达
Comment=arXiv 论文日报与机器人圈动态
Exec=xdg-open http://127.0.0.1:$PORT
Icon=applications-science
Terminal=false
Categories=Science;Education;
DESK
