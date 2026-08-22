import { readFile } from "node:fs/promises";
import { WebSocket } from "ws";

function encodeBodyFrame(requestId, body) {
  const requestIdBuffer = Buffer.from(requestId, "utf8");
  const payload = Buffer.from(body);
  const frame = Buffer.allocUnsafe(2 + requestIdBuffer.length + payload.length);
  frame.writeUInt16BE(requestIdBuffer.length, 0);
  requestIdBuffer.copy(frame, 2);
  payload.copy(frame, 2 + requestIdBuffer.length);
  return frame;
}

function decodeBodyFrame(frame) {
  const payload = Buffer.from(frame);
  const requestIdLength = payload.readUInt16BE(0);
  return {
    requestId: payload.subarray(2, 2 + requestIdLength).toString("utf8"),
    body: payload.subarray(2 + requestIdLength),
  };
}

function filterHeaders(headers) {
  const blocked = new Set(["connection", "keep-alive", "host", "transfer-encoding", "upgrade", "x-local-agent-token"]);
  return Object.fromEntries([...headers.entries()].filter(([name]) => !blocked.has(name.toLowerCase())));
}

function targetUrl(route, path) {
  if (!path.startsWith("/")) throw new Error("上游路径必须以 / 开头");
  const target = new URL(route.targetBaseUrl);
  const requested = new URL(path, "http://agent.invalid");
  const targetPrefix = target.pathname.replace(/\/$/, "");
  const targetPath = requested.pathname === targetPrefix || requested.pathname.startsWith(`${targetPrefix}/`)
    ? requested.pathname
    : `${targetPrefix}/${requested.pathname.replace(/^\/+/, "")}`;
  return `${target.origin}${targetPath}${requested.search}`;
}

async function loadConfig() {
  const configPath = process.env.AGENT_CONFIG || "config.json";
  const config = JSON.parse(await readFile(configPath, "utf8"));
  if (!config.relayUrl || !config.agentId || !config.agentToken || !Array.isArray(config.routes) || !config.routes.length) {
    throw new Error("Agent 配置缺少 relayUrl、agentId、agentToken 或 routes");
  }
  for (const route of config.routes) {
    if (!route.id || !/^https?:\/\//.test(route.targetBaseUrl || "")) {
      throw new Error("每条 route 都需要 id 和 HTTP(S) targetBaseUrl");
    }
    if (route.apiKey !== undefined && typeof route.apiKey !== "string") {
      throw new Error(`route ${route.id} 的 apiKey 必须是字符串`);
    }
    if (route.apiKeyEnv && !process.env[route.apiKeyEnv]) {
      throw new Error(`route ${route.id} 的环境变量 ${route.apiKeyEnv} 未设置`);
    }
  }
  return config;
}

async function start(config) {
  const routes = new Map(config.routes.map((route) => [route.id, route]));
  const inFlight = new Map();
  const socket = new WebSocket(config.relayUrl);
  socket.on("open", () => socket.send(JSON.stringify({
    type: "register",
    agentId: config.agentId,
    token: config.agentToken,
    routes: config.routes.map(({ id, name, provider = "openai_generic" }) => ({ id, name: name || id, provider })),
  })));
  socket.on("message", async (message, isBinary) => {
    try {
      if (isBinary) {
        const { requestId, body } = decodeBodyFrame(message);
        const request = inFlight.get(requestId);
        if (request) request.body.push(body);
        return;
      }
      const frame = JSON.parse(message.toString());
      if (frame.type === "pong" || frame.type === "registered") return;
      if (frame.type === "cancel") {
        inFlight.get(frame.requestId)?.controller.abort();
        return;
      }
      if (frame.type === "request") {
        if (!routes.has(frame.routeId)) throw new Error("Relay 请求了未注册 route");
        inFlight.set(frame.requestId, { frame, body: [], controller: new AbortController() });
        return;
      }
      if (frame.type === "request-end") await forwardRequest(socket, routes, inFlight, frame.requestId);
    } catch (error) {
      console.error("处理 Relay 帧失败", error.message);
    }
  });
  socket.on("close", () => reconnect(config));
  socket.on("error", () => socket.close());
}

async function forwardRequest(socket, routes, inFlight, requestId) {
  const request = inFlight.get(requestId);
  if (!request) return;
  const { frame, body, controller } = request;
  try {
    const route = routes.get(frame.routeId);
    const headers = new Headers(frame.headers);
    headers.delete("authorization");
    headers.delete("x-api-key");
    const apiKey = route.apiKey ?? (route.apiKeyEnv ? process.env[route.apiKeyEnv] : undefined);
    if (apiKey) headers.set("Authorization", `Bearer ${apiKey}`);
    const response = await fetch(targetUrl(route, frame.path), {
      method: frame.method,
      headers: filterHeaders(headers),
      body: body.length ? Buffer.concat(body) : undefined,
      signal: controller.signal,
    });
    socket.send(JSON.stringify({ type: "response-start", requestId, statusCode: response.status, headers: filterHeaders(response.headers) }));
    if (response.body) {
      for await (const chunk of response.body) socket.send(encodeBodyFrame(requestId, chunk));
    }
    socket.send(JSON.stringify({ type: "response-end", requestId }));
  } catch (error) {
    socket.send(JSON.stringify({ type: "error", requestId, message: controller.signal.aborted ? "上游请求已取消" : "本地上游请求失败" }));
  } finally {
    inFlight.delete(requestId);
  }
}

function reconnect(config, attempt = 0) {
  const delay = Math.min(30000, 1000 * 2 ** attempt) + Math.floor(Math.random() * 500);
  setTimeout(() => start(config).catch((error) => { console.error("连接 Relay 失败", error.message); reconnect(config, attempt + 1); }), delay);
}

loadConfig().then((config) => start(config)).catch((error) => {
  console.error("启动 Agent 失败", error.message);
  process.exitCode = 1;
});