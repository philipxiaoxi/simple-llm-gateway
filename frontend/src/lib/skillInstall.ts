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
