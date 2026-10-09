# 云端向 GitHub 推送并开 PR 说明

适用环境：monkeycode 云端（`/workspace`）。仓库：`philipxiaoxi/simple-llm-gateway`。

下次给 Agent 一句话即可：「按 `docs/github-push-runbook.md` 推送并开 PR」。

## 1. 取凭据（每次现取，约 1 小时有效）

- 走 git 凭据助手：
  ```bash
  printf 'protocol=https\nhost=github.com\n\n' | git credential fill
  ```
- 或直接读 unix socket：`/tmp/codingmatrix-git-credential.sock`，请求
  `GET /git-credential?protocol=https&host=github.com`。
- 返回 JSON `{username, password}`，`password` 形如 `ghs_<id>_<JWT>`。
- 返回的 `username` 是占位值，实际使用时固定为 `x-access-token`。
- 全程不要打印 token 明文。

## 2. 先试普通推送

```bash
GIT_TERMINAL_PROMPT=0 git -c credential.helper=/tmp/gh-cred.sh push -u origin <branch>
```

`/tmp/gh-cred.sh` 的作用是强制 `username=x-access-token`。若成功，直接开 PR（第 5 步）。

若报 `Invalid username or token. Password authentication is not supported for Git operations.`：
说明该 App 令牌被 git 端点拒绝，改走 REST API（第 3~5 步）。当前环境就是这种情况。

## 3. 确认 REST API 可写

- `POST /repos/{repo}/git/refs` 建一个临时分支再删，返回 `201` / `204` 即可写。
- `GET /repos/{repo}` 里的 `permissions` 字段对公开仓库可能全为 `false`，
  **不代表没有写权限**，不要据此下结论。

## 4. 用 Git Data API 推送（git push 不可用时的替代）

对 `git rev-list --reverse <base>..HEAD` 的每个提交，按时间顺序处理：

1. 取改动文件：
   ```bash
   git diff-tree --no-commit-id --name-status -r <sha>
   ```
2. 新增/修改的文件：`git ls-tree <sha> -- <path>` 取 `mode` 与 blob sha，
   `git cat-file blob <blob_sha>` 取内容，然后
   `POST /repos/{repo}/git/blobs`（`encoding=base64`）建 blob。
3. `POST /repos/{repo}/git/trees`：`base_tree` = 上一个提交的 tree，
   `tree` = `[{path, mode, type:"blob", sha}]`（删除的条目用 `sha: null`）。
4. `POST /repos/{repo}/git/commits`：`tree` + `parents:[上一个提交 sha]`，
   author/committer 沿用原提交信息。
5. 基座选择：第一个提交的 base 必须是**原分支的父提交 sha**，不要用 `main` 最新 tip，
   否则会把 main 上已有的其它提交回退掉。

处理完后创建/更新分支引用：

- 新建：`POST /repos/{repo}/git/refs`，body `{ref:"refs/heads/<branch>", sha:<最后提交>}`
- 已存在：`PATCH /repos/{repo}/git/refs/heads/<branch>`，body `{sha:<最后提交>, force:true}`

请求头统一：

```
Authorization: token <password>
Accept: application/vnd.github+json
User-Agent: <任意非空字符串>
```

## 5. 开 PR

```
POST https://api.github.com/repos/philipxiaoxi/simple-llm-gateway/pulls
{
  "title": "...",
  "head": "<branch>",
  "base": "main",
  "body": "..."
}
```

响应里的 `html_url` 即 PR 地址。

## 6. 本地与远端对齐

```bash
git fetch origin <branch>
git reset --hard FETCH_HEAD
git branch --set-upstream-to=origin/<branch>
```

## 注意事项

- 网络直连 `github.com`，本环境无代理。
- REST API 偶发 TLS reset（`SSLZeroReturnError`），脚本加 2~5 次重试即可。
- 收尾核对：`GET /repos/{repo}/pulls/<n>/files` 的文件列表应只含本次改动，
  且 `mergeable_state` 为 `clean`。
- 若某次 `git push` 直接成功，则无需第 3~6 步的 API 流程。
