#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import { WebSocket } from "ws";
import { backspace, fillInput } from "./input.js";

const LOG_PREFIX = "[voice-bridge]";

function log(...args) {
  console.log(LOG_PREFIX, ...args);
}

function parseArgs() {
  const argv = process.argv.slice(2);
  const options = { server: "", room: "", method: "auto", config: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--server" || arg === "-s") options.server = argv[++index] || "";
    else if (arg === "--room" || arg === "-r") options.room = (argv[++index] || "").trim().toUpperCase();
    else if (arg === "--method" || arg === "-m") options.method = argv[++index] || "auto";
    else if (arg === "--config" || arg === "-c") options.config = argv[++index] || "";
    else if (arg === "--help" || arg === "-h") {
      console.log(
        [
          "voice-bridge — AI一体化服务平台语音输入桌面端",
          "",
          "用法:",
          "  node src/index.js --server <服务器地址> --room <房间码> [选项]",
          "",
          "选项:",
          "  -s, --server   服务器地址，例如 https://gateway.example.com 或 ws://127.0.0.1:8000",
          "  -r, --room     房间码（管理后台创建）",
          "  -m, --method   输入方式: auto(默认) / type / clipboard",
          "  -c, --config   从 JSON 配置文件读取 server / room / method",
          "",
          "配置示例 config.json:",
          '  {"server": "https://gateway.example.com", "room": "A8K3PZ", "method": "auto"}',
          "",
        ].join("\n"),
      );
      process.exit(0);
    }
  }
  return options;
}

async function loadConfigFile(options) {
  if (!options.config) return;
  try {
    const raw = JSON.parse(await readFile(options.config, "utf8"));
    options.server = options.server || raw.server || "";
    options.room = options.room || (raw.room || "").toString().trim().toUpperCase();
    options.method = options.method === "auto" ? raw.method || "auto" : options.method;
  } catch (error) {
    log("读取配置文件失败:", error.message);
  }
}

function normalizeWsUrl(server) {
  let url = server.trim();
  if (!url) return "";
  if (url.startsWith("ws://") || url.startsWith("wss://")) return url;
  return url.replace(/^https:\/\//, "wss://").replace(/^http:\/\//, "ws://");
}

function buildUrl(server, room) {
  const base = normalizeWsUrl(server);
  return `${base.replace(/\/+$/, "")}/api/voice/ws/${encodeURIComponent(room)}`;
}

function connect(options) {
  const url = buildUrl(options.server, options.room);
  log(`连接房间 ${options.room} → ${url}`);

  const socket = new WebSocket(url);
  const session = { seq: 0, typedLen: 0 };

  function didReallyInput(result) {
    return result.method === "type" || (result.method === "clipboard" && !result.manual);
  }

  function sendAck(seq, result) {
    if (socket.readyState !== WebSocket.OPEN) return;
    socket.send(
      JSON.stringify({
        type: "ack",
        room: options.room,
        seq,
        ok: result.method !== "none" && result.method !== "skipped",
        method: result.method,
        warn: result.warn || "",
      }),
    );
  }

  socket.on("open", () => {
    log(`已连接房间 ${options.room}。现在可以用手机按住说话，文字会自动填入当前输入框。`);
    log(`输入方式: ${options.method === "auto" ? "自动（优先剪贴板+粘贴，失败则逐字输入）" : options.method}`);
  });

  socket.on("message", (data) => {
    let frame;
    try {
      frame = JSON.parse(data.toString());
    } catch {
      return;
    }
    if (frame.type === "connected") {
      log(`服务端确认加入房间 ${frame.room}`);
      return;
    }
    if (frame.type === "transcript") {
      const seq = Number(frame.seq) || 0;
      if (seq !== session.seq) {
        session.seq = seq;
        session.typedLen = 0;
      }
      const text = String(frame.text || "");
      const trimmed = text.replace(/\s+$/, "");
      const result = fillInput(text, options.method);
      if (didReallyInput(result)) {
        session.typedLen += trimmed.length;
        log(`识别 #${seq} 已输入：${text}`);
      } else if (result.method === "skipped") {
        log(`识别 #${seq} 为空，跳过`);
      } else {
        log(`识别 #${seq} 输入失败：${result.warn || "系统缺少所需工具"}`);
      }
      return;
    }
    if (frame.type === "optimized") {
      const seq = Number(frame.seq) || 0;
      if (seq === session.seq && session.typedLen > 0) {
        const rewind = backspace(session.typedLen);
        if (rewind.ok) {
          log(`已回退识别文本 ${session.typedLen} 字符`);
        } else {
          log(`回退失败：${rewind.error || "系统缺少所需工具"}`);
        }
      }
      session.seq = seq;
      session.typedLen = 0;
      const text = String(frame.text || "");
      log(`收到优化结果 #${seq}：${text}`);
      const result = fillInput(text, options.method);
      if (didReallyInput(result)) {
        log("已填入优化后的文字");
      } else if (result.method === "skipped") {
        log("优化文本为空，跳过");
      } else {
        log("优化文本填入失败：", result.warn || "系统缺少所需工具");
      }
      sendAck(seq, result);
      return;
    }
  });

  socket.on("error", (error) => {
    log("WebSocket 错误:", error.message);
  });

  socket.on("close", (code, reason) => {
    log(`连接断开 (code=${code}${reason ? `, reason=${reason}` : ""})，3 秒后重连…`);
    setTimeout(() => connect(options), 3000);
  });

  process.on("SIGINT", () => {
    log("退出");
    socket.close();
    process.exit(0);
  });
}

async function main() {
  const options = parseArgs();
  await loadConfigFile(options);
  if (!options.server || !options.room) {
    console.error(
      LOG_PREFIX,
      "缺少 --server 或 --room。运行 node src/index.js --help 查看用法。",
    );
    process.exit(1);
  }
  connect(options);
}

main();
