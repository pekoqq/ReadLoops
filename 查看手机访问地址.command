#!/bin/bash
# 双击这个文件，会显示手机该打开的网址。
cd "$(dirname "$0")"
echo
echo "================================================"
echo "  在手机上打开下面这个网址（手机要和这台 Mac"
echo "  连同一个 Wi-Fi，或连同一个手机热点）："
echo "================================================"
echo
for iface in en0 en1 en2; do
  ip=$(ipconfig getifaddr $iface 2>/dev/null)
  [ -n "$ip" ] && echo "    http://$ip:8000"
done
# Tailscale 地址（若已开启）
ts=$(/Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4 2>/dev/null | head -1)
[ -n "$ts" ] && echo "    http://$ts:8000   ← Tailscale（不在同一网络时用这个）"
echo
echo "  提示：在 Safari 里打开后，点「分享 → 添加到主屏幕」，"
echo "        就会像一个 App 一样全屏显示。"
echo
read -p "按回车关闭…" _
