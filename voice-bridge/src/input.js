import { spawn, spawnSync } from "node:child_process";
import { platform } from "node:os";

/**
 * 把收到的文本自动填入电脑当前聚焦的输入框。
 *
 * 三种模式：
 * - auto（默认）：优先用剪贴板 + 模拟粘贴快捷键，找不到所需命令时逐字符打字，再失败则打印提示
 * - type：逐字符模拟键盘输入（xdotool / osascript / powershell）
 * - clipboard：只写入剪贴板并提示手动粘贴
 */

function runCommand(command, args, input) {
  const result = spawnSync(command, args, {
    input,
    encoding: "utf8",
    timeout: 8000,
    windowsHide: true,
  });
  return { ok: result.status === 0 && !result.error, error: result.stderr || "", output: result.stdout || "" };
}

function available(command) {
  const probe = runCommand("command", ["-v", command]);
  return probe.ok && probe.output.trim().length > 0;
}

function linuxHasClipboardTool() {
  return available("xclip") || available("xsel") || available("wl-copy");
}

function setClipboardLinux(text) {
  if (available("xclip")) {
    return runCommand("xclip", ["-selection", "clipboard", "-i"], text);
  }
  if (available("xsel")) {
    return runCommand("xsel", ["--clipboard", "--input"], text);
  }
  if (available("wl-copy")) {
    return runCommand("wl-copy", [], text);
  }
  return { ok: false, error: "未找到 xclip / xsel / wl-copy" };
}

function pasteShortcutLinux() {
  return runCommand("xdotool", ["key", "--clearmodifiers", "ctrl+v"]);
}

function typeTextLinux(text) {
  if (!available("xdotool")) {
    return { ok: false, error: "未安装 xdotool，无法自动输入" };
  }
  // 拆成多段防止参数过长；逐段执行，保证输入顺序。
  const segments = text.match(/.{1,2000}/gs) || [text];
  for (const segment of segments) {
    const result = runCommand("xdotool", ["type", "--clearmodifiers", "--delay", "8", segment]);
    if (!result.ok) return result;
  }
  return { ok: true };
}

function setClipboardMac(text) {
  return runCommand("pbcopy", [], text);
}

function pasteShortcutMac() {
  return runCommand(
    "osascript",
    ["-e", 'tell application "System Events" to keystroke "v" using {command down}'],
  );
}

function typeTextMac(text) {
  const escaped = text
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/\n/g, "\\n");
  return runCommand("osascript", ["-e", `tell application "System Events" to keystroke "${escaped}"`]);
}

function setClipboardWindows(text) {
  const encoded = Buffer.from(text, "utf8").toString("base64");
  const script = `$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encoded}'));Set-Clipboard -Value $t`;
  return runCommand("powershell", ["-NoProfile", "-Command", script]);
}

function pasteShortcutWindows() {
  const script = `Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait('^v')`;
  return runCommand("powershell", ["-NoProfile", "-Command", script]);
}

function typeTextWindows(text) {
  const escaped = text.replace(/[()+^%~{}]/g, (char) => `{${char}}`);
  const script = `Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait('${escaped}')`;
  return runCommand("powershell", ["-NoProfile", "-Command", script]);
}

function fillAuto(text) {
  const os = platform();
  if (os === "darwin") {
    if (available("pbcopy") && available("osascript")) {
      const copy = setClipboardMac(text);
      if (copy.ok) {
        const paste = pasteShortcutMac();
        return paste.ok ? { method: "clipboard" } : { method: "clipboard", warn: paste.error };
      }
    }
    const typed = typeTextMac(text);
    return typed.ok ? { method: "type" } : { method: "none", warn: typed.error };
  }
  if (os === "win32") {
    const paste = pasteShortcutWindows();
    if (paste.ok) {
      const copy = setClipboardWindows(text);
      if (copy.ok) return { method: "clipboard" };
    }
    const typed = typeTextWindows(text);
    return typed.ok ? { method: "type" } : { method: "none", warn: typed.error };
  }
  // linux 及其他
  if (linuxHasClipboardTool() && available("xdotool")) {
    const copy = setClipboardLinux(text);
    if (copy.ok) {
      const paste = pasteShortcutLinux();
      return paste.ok ? { method: "clipboard" } : { method: "clipboard", warn: paste.error };
    }
  }
  const typed = typeTextLinux(text);
  return typed.ok ? { method: "type" } : { method: "none", warn: typed.error };
}

function fillByType(text) {
  const os = platform();
  if (os === "darwin") return typeTextMac(text).ok;
  if (os === "win32") return typeTextWindows(text).ok;
  return typeTextLinux(text).ok;
}

function fillByClipboard(text) {
  const os = platform();
  if (os === "darwin") return setClipboardMac(text).ok;
  if (os === "win32") return setClipboardWindows(text).ok;
  return setClipboardLinux(text).ok;
}

export function fillInput(text, method = "auto") {
  const trimmed = text.replace(/\s+$/, "");
  if (!trimmed) return { method: "skipped" };
  if (method === "type") {
    const ok = fillByType(trimmed);
    return ok ? { method: "type" } : { method: "none", warn: "无法模拟键盘输入，请检查系统工具" };
  }
  if (method === "clipboard") {
    const ok = fillByClipboard(trimmed);
    return ok ? { method: "clipboard", manual: true } : { method: "none", warn: "无法写入剪贴板" };
  }
  const result = fillAuto(trimmed);
  if (result.method === "none") {
    const copied = fillByClipboard(trimmed);
    return { method: "none", copied, warn: result.warn };
  }
  return result;
}

function backspaceLinux(count) {
  if (!available("xdotool")) return { ok: false, error: "未安装 xdotool" };
  // 分批发送，单次 --repeat 过大可能超时
  const batch = 200;
  for (let remaining = count; remaining > 0; remaining -= batch) {
    const n = Math.min(remaining, batch);
    const result = runCommand("xdotool", ["key", "--clearmodifiers", "--repeat", String(n), "BackSpace"]);
    if (!result.ok) return result;
  }
  return { ok: true };
}

function backspaceMac(count) {
  const script = `tell application "System Events" to repeat ${count} times\nkey code 51\nend repeat`;
  return runCommand("osascript", ["-e", script]);
}

function backspaceWindows(count) {
  const script = `Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait('{BACKSPACE ${count}}')`;
  return runCommand("powershell", ["-NoProfile", "-Command", script]);
}

/**
 * 模拟按 Backspace 若干次，用于回退之前自动输入的文本（优化结果替换识别结果）。
 * 返回 { ok }。
 */
export function backspace(count) {
  if (!count || count <= 0) return { ok: true };
  const os = platform();
  if (os === "darwin") return backspaceMac(count);
  if (os === "win32") return backspaceWindows(count);
  return backspaceLinux(count);
}

