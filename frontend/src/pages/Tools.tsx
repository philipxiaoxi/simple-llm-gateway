import { useEffect, useState } from "react";
import {
  Clock3,
  Download,
  FileCode2,
  HardDriveDownload,
  History,
  Plus,
  Search,
  Square,
  Terminal,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  getToken,
  type DesktopTool,
  type DesktopToolRun,
} from "../lib/api";
import {
  Badge,
  Button,
  Card,
  Dialog,
  Field,
  Input,
  Select,
} from "../components/ui";
import { errorMessage, formatBytes } from "../lib/utils";
import { notifyBad, notifyOk } from "../lib/toast";

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

const statusText: Record<DesktopTool["status"], string> = {
  not_downloaded: "未下载",
  downloading: "下载中",
  downloaded: "已下载",
  failed: "失败",
};
const statusTone: Record<
  DesktopTool["status"],
  "mist" | "warn" | "ok" | "bad"
> = {
  not_downloaded: "mist",
  downloading: "warn",
  downloaded: "ok",
  failed: "bad",
};

function ToolEditDialog({
  tool,
  onClose,
}: {
  tool: DesktopTool;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(tool.name);
  const [description, setDescription] = useState(tool.description);
  const [platform, setPlatform] = useState(tool.platform);
  const [script, setScript] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    void api
      .desktopToolScript(tool.id)
      .then((result) => setScript(result.script))
      .catch((caught) => setError(errorMessage(caught, "脚本读取失败")));
  }, [tool.id]);
  async function save() {
    try {
      await api.updateDesktopTool(tool.id, { name, description, platform });
      await api.saveDesktopToolScript(tool.id, script);
      await queryClient.invalidateQueries({ queryKey: ["desktop-tools"] });
      notifyOk("工具配置已保存");
      onClose();
    } catch (caught) {
      setError(errorMessage(caught, "保存失败"));
    }
  }
  return (
    <Dialog title={`编辑 ${tool.name}`} onClose={onClose} className="max-w-3xl">
      <div className="grid gap-4">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="名称">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="平台">
            <Select
              value={platform}
              onChange={(event) => setPlatform(event.target.value)}
            >
              <option value="windows">Windows</option>
              <option value="mac">macOS</option>
              <option value="linux">Linux</option>
            </Select>
          </Field>
        </div>
        <Field label="描述">
          <Input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <Field label="Python 脚本">
          <textarea
            value={script}
            onChange={(event) => setScript(event.target.value)}
            className="min-h-72 w-full rounded-md border border-line bg-ink p-3 font-mono text-xs text-paper outline-none focus:border-signal/70"
            spellCheck={false}
          />
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={() => void save()}>保存配置</Button>
        </div>
      </div>
    </Dialog>
  );
}

function ToolLogDialog({
  tool,
  onClose,
}: {
  tool: DesktopTool;
  onClose: () => void;
}) {
  const [lines, setLines] = useState<string[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const response = await fetch(`/api/admin/tools/${tool.id}/events`, {
        headers: { Authorization: `Bearer ${getToken()}` },
        signal: controller.signal,
      });
      if (!response.body) return;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        while (!controller.signal.aborted) {
          const chunk = await reader.read();
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true });
          const parts = buffer.split("\n\n");
          buffer = parts.pop() || "";
          for (const part of parts) {
            const data = part
              .split("\n")
              .find((line) => line.startsWith("data: "));
            if (data)
              setLines((current) => [
                ...current,
                JSON.parse(data.slice(6)).line,
              ]);
          }
        }
      } catch {
        /* 对话框关闭时终止读取 */
      }
    })();
    return () => controller.abort();
  }, [tool.id]);
  return (
    <Dialog
      title={`${tool.name} · 执行日志`}
      onClose={onClose}
      className="max-w-3xl"
    >
      <div className="max-h-[55vh] overflow-auto rounded-md border border-line bg-ink p-3 font-mono text-xs leading-6 text-mist">
        {lines.length
          ? lines.map((line, index) => (
              <div key={`${index}-${line}`}>{line}</div>
            ))
          : "等待脚本输出…"}
      </div>
    </Dialog>
  );
}

function ToolHistoryDialog({
  tool,
  onClose,
}: {
  tool: DesktopTool;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<DesktopToolRun | null>(null);
  const { data: runs = [], isLoading } = useQuery({
    queryKey: ["desktop-tool-runs", tool.id],
    queryFn: () => api.desktopToolRuns(tool.id),
  });
  const detail = useQuery({
    queryKey: ["desktop-tool-run", tool.id, selected?.id],
    queryFn: () => api.desktopToolRun(tool.id, selected!.id),
    enabled: selected !== null,
  });
  const runStatus: Record<DesktopToolRun["status"], string> = {
    running: "执行中",
    downloaded: "成功",
    failed: "失败",
    stopped: "已停止",
  };
  return (
    <Dialog
      title={`${tool.name} · 执行历史`}
      onClose={onClose}
      className="max-w-4xl"
    >
      <div className="grid gap-4 md:grid-cols-[minmax(12rem,0.8fr)_minmax(0,1.7fr)]">
        <div className="max-h-[55vh] space-y-2 overflow-auto">
          {isLoading ? <div className="text-sm text-mist">加载中…</div> : null}
          {!isLoading && !runs.length ? (
            <div className="text-sm text-mist">暂无执行记录</div>
          ) : null}
          {runs.map((run) => (
            <button
              key={run.id}
              type="button"
              onClick={() => setSelected(run)}
              className={`w-full rounded-md border p-3 text-left transition ${selected?.id === run.id ? "border-signal/70 bg-panel-2" : "border-line bg-ink hover:border-mist/50"}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs text-paper">#{run.id}</span>
                <Badge
                  tone={
                    run.status === "downloaded"
                      ? "ok"
                      : run.status === "running"
                        ? "warn"
                        : "bad"
                  }
                >
                  {runStatus[run.status]}
                </Badge>
              </div>
              <div className="mt-2 flex items-center gap-1 text-xs text-mist">
                <Clock3 size={13} />
                {new Date(run.started_at).toLocaleString()}
              </div>
              {run.error_message ? (
                <div className="mt-2 line-clamp-2 text-xs text-danger">
                  {run.error_message}
                </div>
              ) : null}
            </button>
          ))}
        </div>
        <div className="max-h-[55vh] overflow-auto rounded-md border border-line bg-ink p-3 font-mono text-xs leading-6 text-mist">
          {detail.isFetching
            ? "加载日志中…"
            : detail.data?.lines.length
              ? detail.data.lines.map((line, index) => (
                  <div key={`${index}-${line}`}>{line}</div>
                ))
              : selected
                ? "该执行没有输出日志"
                : "选择一条执行记录查看日志"}
        </div>
      </div>
    </Dialog>
  );
}

function ToolCreateDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [toolId, setToolId] = useState("");
  const [platform, setPlatform] = useState("windows");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [script, setScript] = useState(
    "import json, pathlib, sys\n\npayload = json.loads(sys.argv[1])\n# 下载文件后打印最终 JSON\n",
  );
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function save() {
    if (!toolId.trim() || !name.trim() || !script.trim()) {
      setError("请填写工具 ID、名称和 Python 脚本");
      return;
    }
    setPending(true);
    setError("");
    try {
      await api.createDesktopTool({
        tool_id: toolId.trim(),
        platform,
        name: name.trim(),
        description: description.trim(),
        script,
      });
      await queryClient.invalidateQueries({ queryKey: ["desktop-tools"] });
      notifyOk("工具已登记");
      onClose();
    } catch (caught) {
      setError(errorMessage(caught, "登记失败"));
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog title="登记桌面工具" onClose={onClose} className="max-w-3xl">
      <div className="grid gap-4">
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="工具 ID">
            <Input
              value={toolId}
              onChange={(event) => setToolId(event.target.value)}
              placeholder="chatgpt"
            />
          </Field>
          <Field label="平台">
            <Select
              value={platform}
              onChange={(event) => setPlatform(event.target.value)}
            >
              <option value="windows">Windows</option>
              <option value="mac">macOS</option>
              <option value="linux">Linux</option>
            </Select>
          </Field>
          <Field label="名称">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="ChatGPT Desktop"
            />
          </Field>
        </div>
        <Field label="描述">
          <Input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="工具说明"
          />
        </Field>
        <Field label="Python 脚本">
          <textarea
            value={script}
            onChange={(event) => setScript(event.target.value)}
            className="min-h-72 w-full rounded-md border border-line bg-ink p-3 font-mono text-xs text-paper outline-none focus:border-signal/70"
            spellCheck={false}
          />
        </Field>
        <p className="text-xs leading-5 text-mist">
          脚本启动参数是 JSON：tool_id、platform、output_dir。结束时请向 stdout
          打印包含 status、file_path、file_size、version、error_message 的最终
          JSON。
        </p>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button disabled={pending} onClick={() => void save()}>
            {pending ? "登记中…" : "登记工具"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

export function ToolsPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [edit, setEdit] = useState<DesktopTool | null>(null);
  const [logs, setLogs] = useState<DesktopTool | null>(null);
  const [history, setHistory] = useState<DesktopTool | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const { data = [], isFetching } = useQuery({
    queryKey: ["desktop-tools"],
    queryFn: api.desktopTools,
    refetchInterval: 1500,
  });
  const items = data.filter((item) =>
    `${item.name} ${item.description} ${item.platform}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const preDownload = useMutation({
    mutationFn: api.preDownloadTool,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["desktop-tools"] });
      notifyOk("已开始更新缓存");
    },
    onError: (caught) => notifyBad(errorMessage(caught, "更新缓存失败")),
  });
  const stopDownload = useMutation({
    mutationFn: api.stopDownloadTool,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["desktop-tools"] });
      notifyOk("已停止下载");
    },
    onError: (caught) => notifyBad(errorMessage(caught, "停止失败")),
  });
  async function download(tool: DesktopTool) {
    try {
      triggerDownload(
        await api.downloadDesktopTool(tool.id),
        tool.file_name || `${tool.tool_id}-${tool.platform}`,
      );
      notifyOk("已开始下载");
    } catch (caught) {
      notifyBad(errorMessage(caught, "下载失败"));
    }
  }
  const counts = {
    total: data.length,
    downloaded: data.filter((item) => item.status === "downloaded").length,
    active: data.filter((item) => item.status === "downloading").length,
  };
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">工具中心</h1>
          <p className="mt-1 text-sm text-mist">
            配置桌面工具脚本，预下载到服务器缓存后交付安装包。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone="info">
            <Terminal size={14} />
            脚本执行最长 60 分钟
          </Badge>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus size={16} />
            登记工具
          </Button>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">
            工具总数
          </div>
          <div className="mt-3 font-mono text-3xl text-signal">
            {isFetching && !data.length ? "—" : counts.total}
          </div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">
            已缓存
          </div>
          <div className="mt-3 font-mono text-3xl text-info">
            {counts.downloaded}
          </div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">
            执行中
          </div>
          <div className="mt-3 font-mono text-3xl text-warn">
            {counts.active}
          </div>
        </Card>
      </div>
      <Card>
        <Field label="搜索工具">
          <div className="relative">
            <Search
              size={16}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist"
            />
            <Input
              className="pl-9"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索名称、描述、平台"
            />
          </div>
        </Field>
      </Card>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {items.map((tool) => (
          <Card key={tool.id} className="flex flex-col gap-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">{tool.name}</h2>
                <code className="text-xs text-mist">
                  {tool.tool_id} · {tool.platform}
                </code>
              </div>
              <Badge tone={statusTone[tool.status]}>
                {statusText[tool.status]}
              </Badge>
            </div>
            <p className="min-h-12 text-sm text-mist">
              {tool.description || "暂无描述"}
            </p>
            {tool.error_message ? (
              <div className="line-clamp-2 text-xs text-danger">
                {tool.error_message}
              </div>
            ) : null}
            <div className="mt-auto flex items-center justify-between text-xs text-mist">
              <span>
                {tool.file_size ? formatBytes(tool.file_size) : "尚未生成文件"}
                {tool.version ? ` · v${tool.version}` : ""}
              </span>
              <span>{tool.script_name}</span>
            </div>
            <div className="flex gap-2">
              <Button
                variant="line"
                className="flex-1"
                onClick={() => setEdit(tool)}
              >
                <FileCode2 size={16} />
                编辑
              </Button>
              {tool.status === "downloading" ? (
                <Button
                  variant="danger"
                  className="flex-1"
                  disabled={stopDownload.isPending}
                  onClick={() => stopDownload.mutate(tool.id)}
                >
                  <Square size={16} />
                  停止
                </Button>
              ) : (
                <Button
                  variant="line"
                  className="flex-1"
                  onClick={() => {
                    setLogs(tool);
                    preDownload.mutate(tool.id);
                  }}
                >
                  <HardDriveDownload size={16} />
                  {tool.status === "downloaded" ? "更新缓存" : "下载缓存"}
                </Button>
              )}
              <Button
                disabled={tool.status !== "downloaded"}
                onClick={() => void download(tool)}
              >
                <Download size={16} />
                下载
              </Button>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <button
                type="button"
                className="flex items-center gap-1 text-info hover:text-paper"
                onClick={() => setHistory(tool)}
              >
                <History size={14} />
                执行历史
              </button>
              {tool.status === "downloading" ? (
                <button
                  type="button"
                  className="text-info hover:text-paper"
                  onClick={() => setLogs(tool)}
                >
                  查看实时日志
                </button>
              ) : null}
            </div>
          </Card>
        ))}
      </div>
      {createOpen ? (
        <ToolCreateDialog onClose={() => setCreateOpen(false)} />
      ) : null}
      {logs ? (
        <ToolLogDialog tool={logs} onClose={() => setLogs(null)} />
      ) : null}
      {history ? (
        <ToolHistoryDialog tool={history} onClose={() => setHistory(null)} />
      ) : null}
      {edit ? (
        <ToolEditDialog tool={edit} onClose={() => setEdit(null)} />
      ) : null}
    </div>
  );
}
