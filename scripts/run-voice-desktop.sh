#!/usr/bin/env bash
# 启动电脑端语音注入客户端。
# 用法：先在后台「语音房」页面点「复制电脑端配置」，把内容存成 ~/.llm-gateway-voice.json，然后跑本脚本。
set -euo pipefail
cd "$(dirname "$0")/../voice-desktop"
if [[ ! -d node_modules ]]; then
  echo "正在安装依赖…"
  npm install
fi
echo "提示：首次使用需要在「系统设置 → 隐私与安全性 → 辅助功能」里给本终端打勾，并允许控制 System Events。"
echo "启动后默认不注入，按 i 进入插入模式才会写入输入框。"
exec node src/index.js "$@"
