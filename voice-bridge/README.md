# voice-bridge — 手机语音输入到电脑的桌面端小程序

把「AI一体化服务平台」语音输入房间的转写结果，自动填入电脑上当前聚焦的输入框。

## 工作原理

1. 管理后台「语音输入」页创建一个房间
2. 电脑上运行本小程序，用房间码连上服务器（WebSocket）
3. 手机打开房间的手机端链接，按住按钮说话
4. 服务器把录音转成文字、用大模型优化表达后，通过 WebSocket 推送到本程序
5. 本程序自动把文字填进当前激活的输入框

## 安装

```bash
cd voice-bridge
npm install
```

## 用法

### 方式一：命令行参数

```bash
node src/index.js --server https://你的服务器地址 --room 房间码
```

### 方式二：配置文件

创建 `config.json`：

```json
{
  "server": "https://你的服务器地址",
  "room": "房间码",
  "method": "auto"
}
```

然后运行：

```bash
node src/index.js --config config.json
```

## 输入方式说明

| 方式 | 说明 | 适用 |
|------|------|------|
| `auto`（默认） | 优先复制到剪贴板并模拟 Ctrl/Cmd+V 粘贴；粘贴失败则逐字模拟键盘输入 | 大多数场景 |
| `type` | 逐字模拟键盘输入 | 剪贴板不可用或想逐步输入 |
| `clipboard` | 只写入剪贴板，手动粘贴 | 无图形环境 / 受限环境 |

Linux 逐字输入依赖 `xdotool`，剪贴板依赖 `xclip` / `xsel` / `wl-copy` 之一：

```bash
sudo apt-get install -y xdotool xclip   # Debian / Ubuntu
```

macOS 依赖系统自带的 `pbcopy` 与 `osascript`，无需额外安装。
Windows 使用 PowerShell，无需额外安装。

## 帮助

```bash
node src/index.js --help
```
