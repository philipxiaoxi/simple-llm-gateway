# 手机语音输入 → 电脑输入框

把手机当麦克风，边说边出字，AI 润色后**原地替换**，文本自动填进电脑的输入框。

## 文档索引

| 文档 | 内容 |
|---|---|
| [技术方案.md](./技术方案.md) | **主文档**。需求分析、架构、文本替换算法、后端/前端/管理页设计、日志、安全、测试、路线图、改动清单 |
| [协议参考.md](./协议参考.md) | 阿里云 DashScope 实时 ASR 协议（**已实测**）+ 本项目自定义 WS/HTTP 协议完整定义 |
| [桌面客户端.md](./桌面客户端.md) | `voice-desktop` Node 客户端设计：注入方式选型、替换算法、权限、测试 |
| [任务拆解.md](./任务拆解.md) | 分阶段任务清单（每条含文件路径与验证方式） |

## 一句话架构

```
手机 H5（按住说话，AudioWorklet 采 PCM）
   └─WSS─► 本站后端（FastAPI，房间 + 直通转发）
              ├─WSS─► 阿里云百炼 Paraformer 实时识别（边说边出字）
              ├─────► 本站上游账号大模型（AI 润色，按房间配置选账号+模型）
              └─WSS─► 电脑 voice-desktop（剪贴板注入 + 尾部替换）
                        所有环节写入 voice_events / voice_segments 日志
```

## 关键前置验证（已完成）

- ✅ 用户提供的 `sk-ws-…` Key 在 `wss://dashscope.aliyuncs.com/api-ws/v1/inference` 上**实测跑通全链路**
- ✅ `qwen-audio-3.0-asr-flash-streaming`、`fun-asr-realtime`、`paraformer-realtime-v2` 均可用同一份 `run-task` 报文跑通（→ 二进制 PCM → `result-generated` → `finish-task`），**默认选 qwen-audio-3.0-asr-flash-streaming**
- ✅ 已用真实中文语音实测三者的转写效果与中间结果密度（见 [技术方案.md §4.4.1](./技术方案.md)）
- ✅ 已实测 ASR 自身的"润色"边界：默认配置下**一个赘词都不删**；开启 `disfluency_removal_enabled` 后 Qwen 能删干净语气词并重组标点，但**修不了「嗯→问」「吧→把」这类同音字错**——这正是文本大模型唯一不可替代的价值（见 [技术方案.md §4.4.3](./技术方案.md)）
- ❌ `qwen3-asr-flash-realtime` 协议不同（会话式）、不支持时间戳、不支持连接复用，**不适合本场景**
- ✅ 现有 venv 已含 `websockets 17.0.1`（需显式写入 requirements.txt）

详见 [协议参考.md §1.1](./协议参考.md)。

## 尚未开始编码

本目录当前只包含**技术方案**。按 [任务拆解.md](./任务拆解.md) 的 P0-1 → P0-5 顺序实施，建议先把 P0-2（裸链路：手机说话 → 电脑出字）跑通，再叠加 AI 润色与文本替换。
