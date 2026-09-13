export function buildSkillInstallText(
  skill: { name: string; slug: string; description?: string | null },
  downloadUrl: string,
) {
  const lines = ['请把下面这个 Claude Code Skill 安装到本机。', '', `名称：${skill.name}`]
  const description = skill.description?.trim()
  if (description) lines.push(`说明：${description}`)
  lines.push(
    `下载地址（5 分钟内有效，过期请重新生成）：${downloadUrl}`,
    '',
    '安装步骤：',
    `1. 下载：curl -L -o /tmp/${skill.slug}.zip "${downloadUrl}"`,
    `2. 解压：unzip -o /tmp/${skill.slug}.zip -d /tmp/${skill.slug}-install`,
    `3. 确认 /tmp/${skill.slug}-install/${skill.slug}/SKILL.md 存在`,
    `4. 把 /tmp/${skill.slug}-install/${skill.slug}/ 移动到 ~/.claude/skills/${skill.slug}/（已存在先备份再覆盖）`,
    '5. 完成后告诉我安装路径',
  )
  return lines.join('\n')
}

export function buildSkillUploadText(uploadUrl: string) {
  return [
    '请把本机各 Agent 的 skills 存放目录中的全部 Skills 上传到网关。',
    '',
    `上传地址（5 分钟内有效，过期请重新生成）：${uploadUrl}`,
    '',
    '步骤：',
    '1. 自行判断本机有哪些 Agent，以及各自的 skills 根目录。',
    '   常见示例（仅供参考，按实际存在的路径处理）：',
    '   - ~/.claude/skills/',
    '   - ~/.cursor/skills/',
    '2. 对每个 skills 根目录：取下一层子目录；目录内有 SKILL.md 则纳入。',
    '3. 打成单个 zip：/tmp/skills-upload.zip',
    '   结构：{slug}/SKILL.md + 其余文件',
    '4. 上传：',
    '   curl -sS -X POST \\',
    '     -F "files=@/tmp/skills-upload.zip;type=application/zip" \\',
    '     -F "category=自动识别" \\',
    `     "${uploadUrl}"`,
    '5. 把返回 JSON（created / items[].name / skipped）原样告诉我',
    '',
    '说明：',
    '- 服务端会把名称改为【ai】-年-月-日 时分秒-原名，与手工上传区分',
    '- 每次上传均为新建，不覆盖已有记录',
    '- 体积上限 20MB',
  ].join('\n')
}
