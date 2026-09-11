/**
 * inject.test.js —— 注入动作编排单测
 *
 * 全部使用依赖注入的假 executor，**不会真的动键盘、不会碰真实剪贴板**。
 * 假 executor 还模拟了一个「输入框 + 光标」的编辑器模型，
 * 因此可以端到端验证「粘贴 / Shift+← 选中 / Delete」的编排结果。
 */
import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import {
  CURSOR_POS_SCRIPT,
  FOREGROUND_APP_SCRIPT,
  PASTE_SCRIPT,
  applyPlan,
  classifyInjectError,
  createClipboardGuard,
  createInjector,
  createMacInjector,
  createUnsupportedInjector,
  explainOsascriptError,
  sleep,
} from '../src/inject.js'
import { SegmentLedger, charLen } from '../src/segments.js'

/**
 * 造一个假 macOS 环境：
 *  - run(cmd, args, { input }) 记录全部调用
 *  - 内置一个极简编辑器模型：box 文本 + sel（从末尾向左选中的字符数，光标恒在末尾）
 */
function createFakeMac({ clipboard = '用户原来的剪贴板', app = 'FakeApp', box = '', sel = 0, failScripts = [] } = {}) {
  const calls = []
  const state = { clipboard, app, box, sel }

  const run = async (cmd, args = [], opts = {}) => {
    const call = { cmd, args: [...args], input: opts.input ?? null }
    calls.push(call)

    if (cmd === 'pbcopy') {
      state.clipboard = String(call.input ?? '')
      return { stdout: '', stderr: '' }
    }
    if (cmd === 'pbpaste') {
      return { stdout: state.clipboard, stderr: '' }
    }
    if (cmd !== 'osascript') throw new Error(`未预期的命令：${cmd}`)

    const script = args[1] ?? ''
    const fail = failScripts.find((f) => script.includes(f))
    if (fail) {
      const err = new Error(`osascript 执行失败：${fail}`)
      err.stderr = 'execution error: Not authorized to send Apple events to System Events. (-1743)'
      throw err
    }

    if (script.includes('keystroke "v"')) {
      // 粘贴：选中内容被覆盖，光标停在末尾
      state.box = state.box.slice(0, state.box.length - state.sel) + state.clipboard
      state.sel = 0
      return { stdout: '', stderr: '' }
    }
    const repeat = /repeat (\d+) times/.exec(script)
    if (repeat && script.includes('key code 123')) {
      state.sel = Math.min(state.box.length, state.sel + Number(repeat[1]))
      return { stdout: '', stderr: '' }
    }
    if (repeat && script.includes('key code 51')) {
      state.box = state.box.slice(0, state.box.length - state.sel)
      state.sel = 0
      return { stdout: '', stderr: '' }
    }
    if (script.includes('AXFocusedUIElement')) {
      return { stdout: `${state.box.length - state.sel}\n`, stderr: '' }
    }
    if (script.includes('name of first application process')) {
      return { stdout: `${state.app}\n`, stderr: '' }
    }
    throw new Error(`未识别的 AppleScript：${script.slice(0, 60)}`)
  }

  const scripts = () => calls.filter((c) => c.cmd === 'osascript').map((c) => c.args[1])
  return {
    run,
    calls,
    state,
    scripts,
    cmds: () => calls.map((c) => c.cmd),
    countOf: (cmd) => calls.filter((c) => c.cmd === cmd).length,
  }
}

describe('macOS 基础动作（依赖注入，不碰真实键盘）', () => {
  it('setClipboard 用 pbcopy + stdin 写入，不经 shell、不拼接参数', async () => {
    const fake = createFakeMac()
    const inj = createMacInjector({ run: fake.run })
    const text = '你好，我们明天见。😀 "quotes" `backtick` $VAR; rm -rf /'
    await inj.setClipboard(text)

    assert.equal(fake.calls.length, 1)
    const [call] = fake.calls
    assert.equal(call.cmd, 'pbcopy')
    assert.deepEqual(call.args, []) // 不把文本拼进参数
    assert.equal(call.input, text) // 走 stdin（runner 内部按 UTF-8 写入）
    assert.equal(fake.state.clipboard, text)
    // 文本绝不能出现在命令行参数里（避免转义/注入问题）
    assert.ok(!JSON.stringify(call.args).includes('rm -rf'))
  })

  it('getClipboard 用 pbpaste', async () => {
    const fake = createFakeMac({ clipboard: '剪贴板内容' })
    const inj = createMacInjector({ run: fake.run })
    assert.equal(await inj.getClipboard(), '剪贴板内容')
    assert.equal(fake.calls[0].cmd, 'pbpaste')
  })

  it('pressPaste 用 osascript 发 Cmd+V', async () => {
    const fake = createFakeMac()
    const inj = createMacInjector({ run: fake.run })
    await inj.pressPaste()
    assert.equal(fake.calls[0].cmd, 'osascript')
    assert.deepEqual(fake.calls[0].args, ['-e', PASTE_SCRIPT])
    assert.match(PASTE_SCRIPT, /keystroke "v" using command down/)
  })

  it('selectBack 分批 Shift+←，用 repeat 减少 osascript 次数', async () => {
    const fake = createFakeMac({ box: '一二三四五六七八九十'.repeat(10) })
    const inj = createMacInjector({ run: fake.run, batch: 20 })
    await inj.selectBack(50)
    assert.equal(fake.countOf('osascript'), 3) // 20 + 20 + 10
    const scripts = fake.scripts()
    assert.match(scripts[0], /repeat 20 times/)
    assert.match(scripts[0], /key code 123 using shift down/)
    assert.match(scripts[2], /repeat 10 times/)
    assert.equal(fake.state.sel, 50)
  })

  it('selectBack(0) / 负数不产生任何调用', async () => {
    const fake = createFakeMac()
    const inj = createMacInjector({ run: fake.run })
    await inj.selectBack(0)
    await inj.selectBack(-5)
    assert.equal(fake.calls.length, 0)
  })

  it('selectBack 支持自定义批大小', async () => {
    const fake = createFakeMac({ box: 'x'.repeat(100) })
    const inj = createMacInjector({ run: fake.run })
    await inj.selectBack(25, 10)
    assert.deepEqual(
      fake.scripts().map((s) => /repeat (\d+) times/.exec(s)[1]),
      ['10', '10', '5'],
    )
  })

  it('pressDelete 用 key code 51', async () => {
    const fake = createFakeMac()
    const inj = createMacInjector({ run: fake.run })
    await inj.pressDelete()
    assert.match(fake.scripts()[0], /key code 51/)
    assert.match(fake.scripts()[0], /repeat 1 times/)
  })

  it('cursorPos 取到值时返回数字', async () => {
    const fake = createFakeMac({ box: '你好我们', sel: 1 })
    const inj = createMacInjector({ run: fake.run })
    assert.equal(await inj.cursorPos(), 3)
    assert.equal(fake.calls[0].args[1], CURSOR_POS_SCRIPT)
  })

  it('cursorPos 取不到（App 不支持）返回 -1，不抛异常', async () => {
    const fake = createFakeMac({ failScripts: ['AXFocusedUIElement'] })
    const inj = createMacInjector({ run: fake.run })
    assert.equal(await inj.cursorPos(), -1)
  })

  it('cursorPos 返回非数字（missing value）时为 -1', async () => {
    const inj = createMacInjector({
      run: async (cmd, args) => {
        if (cmd === 'osascript' && args[1].includes('AXFocusedUIElement')) return { stdout: 'missing value\n', stderr: '' }
        throw new Error('unexpected')
      },
    })
    assert.equal(await inj.cursorPos(), -1)
  })

  it('foregroundApp 返回前台 App 名称，失败时返回「未知」', async () => {
    const fake = createFakeMac({ app: 'WeChat' })
    const inj = createMacInjector({ run: fake.run })
    assert.equal(await inj.foregroundApp(), 'WeChat')
    assert.equal(fake.calls[0].args[1], FOREGROUND_APP_SCRIPT)

    const broken = createMacInjector({
      run: async () => {
        throw new Error('boom')
      },
    })
    assert.equal(await broken.foregroundApp(), '未知')
  })

  it('checkPermission：成功给出前台 App，失败给出中文指引', async () => {
    const okFake = createFakeMac({ app: 'Finder' })
    const ok = await createMacInjector({ run: okFake.run }).checkPermission()
    assert.equal(ok.ok, true)
    assert.equal(ok.app, 'Finder')

    const badFake = createFakeMac({ failScripts: ['name of first application process'] })
    const bad = await createMacInjector({ run: badFake.run }).checkPermission()
    assert.equal(bad.ok, false)
    assert.match(bad.error, /自动化权限|辅助功能/)
    assert.match(bad.hint, /系统设置/)
    assert.match(bad.hint, /辅助功能/)
    assert.match(bad.hint, /System Events/)
  })

  it('checkPermission 超时（卡在系统授权弹窗）不挂死启动，给出明确中文提示', async () => {
    const inj = createMacInjector({
      run: async () => {
        const err = new Error('Command failed: osascript')
        err.killed = true
        err.signal = 'SIGTERM'
        throw err
      },
    })
    const res = await inj.checkPermission({ timeoutMs: 10 })
    assert.equal(res.ok, false)
    assert.equal(res.timedOut, true)
    assert.match(res.error, /超时/)
    assert.match(res.hint, /允许/)
  })

  it('osascript 调用带超时参数，避免卡死主流程', async () => {
    const seen = []
    const inj = createMacInjector({
      run: async (cmd, args, opts = {}) => {
        seen.push({ cmd, timeout: opts.timeout })
        return { stdout: 'FakeApp\n', stderr: '' }
      },
    })
    await inj.foregroundApp()
    assert.ok(seen[0].timeout > 0)
  })
})

describe('平台抽象', () => {
  it('非 macOS 给出清晰中文报错，而不是静默失败', async () => {
    const inj = createInjector({ platform: 'win32' })
    assert.equal(inj.supported, false)
    assert.match(inj.reason, /仅支持 macOS/)
    await assert.rejects(() => inj.pressPaste(), /仅支持 macOS/)
    assert.equal(await inj.cursorPos(), -1)
  })

  it('darwin 返回 macOS 实现', () => {
    const inj = createInjector({ platform: 'darwin', run: createFakeMac().run })
    assert.equal(inj.supported, true)
    for (const m of ['setClipboard', 'getClipboard', 'pressPaste', 'selectBack', 'pressDelete', 'cursorPos', 'foregroundApp']) {
      assert.equal(typeof inj[m], 'function', `缺少方法 ${m}`)
    }
  })

  it('createUnsupportedInjector 直接可用', async () => {
    const inj = createUnsupportedInjector('linux')
    assert.equal(inj.platform, 'linux')
    await assert.rejects(() => inj.setClipboard('x'), /暂不支持/)
  })
})

describe('错误归类', () => {
  it('explainOsascriptError 把 -1743 / -25211 / -10004 翻译成中文', () => {
    assert.match(explainOsascriptError({ stderr: '… (-1743)' }), /自动化权限/)
    assert.match(explainOsascriptError({ message: 'osascript is not allowed assistive access. (-25211)' }), /辅助功能权限/)
    assert.match(explainOsascriptError({ stderr: '…发生权限违例。 (-10004)' }), /-10004/)
    assert.match(explainOsascriptError({ message: '其他错误' }), /其他错误/)
    assert.match(explainOsascriptError({ killed: true }), /超时/)
  })

  it('classifyInjectError 映射到协议 reason', () => {
    assert.equal(classifyInjectError({ reason: 'secure_input' }), 'secure_input')
    assert.equal(classifyInjectError({ message: 'pbcopy failed' }), 'clipboard_failed')
    assert.equal(classifyInjectError({ code: 'ENOENT' }), 'clipboard_failed')
    assert.equal(classifyInjectError({ stderr: 'secure event input enabled' }), 'secure_input')
    assert.equal(classifyInjectError({ message: 'Not authorized (-1743)' }), 'perm_denied')
    assert.equal(classifyInjectError(null), 'perm_denied')
  })
})

describe('剪贴板守卫', () => {
  it('一次注入周期只保存一次原剪贴板', async () => {
    const fake = createFakeMac({ clipboard: '原来的' })
    const inj = createMacInjector({ run: fake.run })
    const guard = createClipboardGuard({ inj, restoreDelayMs: 300 })
    await guard.capture()
    await guard.capture()
    await guard.capture()
    assert.equal(fake.countOf('pbpaste'), 1)
    assert.equal(guard.saved, '原来的')
  })

  it('内容未变 → 还原', async () => {
    const fake = createFakeMac({ clipboard: '原来的' })
    const inj = createMacInjector({ run: fake.run })
    const guard = createClipboardGuard({ inj })
    await guard.capture()
    fake.state.clipboard = '我们注入的文本' // 模拟 setClipboard 覆盖
    guard.noteWritten('我们注入的文本')
    const res = await guard.restoreNow()
    assert.deepEqual(res, { restored: true })
    assert.equal(fake.state.clipboard, '原来的')
    assert.equal(guard.saved, null)
  })

  it('期间被用户改动过 → 放弃还原（不覆盖用户自己的复制）', async () => {
    const fake = createFakeMac({ clipboard: '原来的' })
    const inj = createMacInjector({ run: fake.run })
    const guard = createClipboardGuard({ inj })
    await guard.capture()
    guard.noteWritten('我们注入的文本')
    fake.state.clipboard = '用户自己刚复制的东西' // 用户在还原窗口内复制了新内容
    const res = await guard.restoreNow()
    assert.equal(res.restored, false)
    assert.equal(res.skipped, 'changed')
    assert.equal(fake.state.clipboard, '用户自己刚复制的东西')
  })

  it('scheduleRestore 延时还原，且不阻塞', async () => {
    const fake = createFakeMac({ clipboard: '原来的' })
    const inj = createMacInjector({ run: fake.run })
    const guard = createClipboardGuard({ inj, restoreDelayMs: 10 })
    await guard.capture()
    fake.state.clipboard = '注入的'
    guard.noteWritten('注入的')
    guard.scheduleRestore()
    assert.equal(fake.state.clipboard, '注入的') // 还没到时间
    await sleep(40)
    assert.equal(fake.state.clipboard, '原来的')
  })

  it('新的注入会取消尚未执行的还原', async () => {
    const fake = createFakeMac({ clipboard: '原来的' })
    const inj = createMacInjector({ run: fake.run })
    const guard = createClipboardGuard({ inj, restoreDelayMs: 20 })
    await guard.capture()
    fake.state.clipboard = '第一次注入'
    guard.noteWritten('第一次注入')
    guard.scheduleRestore()
    await guard.capture() // 第二次注入开始 → 取消还原，并沿用最初保存的内容
    fake.state.clipboard = '第二次注入'
    guard.noteWritten('第二次注入')
    guard.scheduleRestore()
    await sleep(60)
    assert.equal(fake.state.clipboard, '原来的')
  })
})

describe('applyPlan 动作编排', () => {
  const mk = (opts) => {
    const fake = createFakeMac(opts)
    const inj = createMacInjector({ run: fake.run })
    const clipboard = createClipboardGuard({ inj, restoreDelayMs: 5 })
    return { fake, inj, clipboard }
  }

  it('insert：写剪贴板 → 粘贴', async () => {
    const { fake, inj, clipboard } = mk()
    const res = await applyPlan(
      { kind: 'insert', segId: 's1', text: '你好', tailNew: '你好', tailOld: 0 },
      { inj, clipboard },
    )
    assert.equal(res.action, 'inserted')
    assert.equal(res.delta, 2)
    assert.deepEqual(fake.cmds(), ['pbpaste', 'pbcopy', 'osascript'])
    assert.equal(fake.state.box, '你好')
  })

  it('append：同样只要一次粘贴，不做选中/回删', async () => {
    const { fake, inj, clipboard } = mk({ box: '你好' })
    const res = await applyPlan(
      { kind: 'append', segId: 's1', text: '你好我们', tailNew: '我们', tailOld: 0 },
      { inj, clipboard },
    )
    assert.equal(res.action, 'appended')
    assert.equal(res.delta, 2)
    assert.deepEqual(fake.cmds(), ['pbpaste', 'pbcopy', 'osascript'])
    assert.equal(fake.scripts().some((s) => s.includes('key code 123')), false)
    assert.equal(fake.state.box, '你好我们')
  })

  it('replace：写剪贴板 → Shift+← 选中 → 粘贴（选中即被覆盖，原子替换）', async () => {
    const { fake, inj, clipboard } = mk({ box: '你好我们' })
    const res = await applyPlan(
      { kind: 'replace', segId: 's1', text: '你好，我们明天', tailOld: 2, tailNew: '，我们明天' },
      { inj, clipboard },
    )
    assert.equal(res.action, 'replaced')
    assert.equal(res.selectedBack, 2)
    assert.equal(res.delta, 3) // 「，我们明天」5 字 - 回删 2 字
    assert.deepEqual(fake.cmds(), ['pbpaste', 'pbcopy', 'osascript', 'osascript'])
    assert.equal(fake.scripts()[0].includes('key code 123'), true)
    assert.equal(fake.scripts()[1], PASTE_SCRIPT)
    assert.equal(fake.state.box, '你好，我们明天')
    assert.equal(fake.state.sel, 0)
  })

  it('delete：Shift+← 选中后按 Delete，不碰剪贴板', async () => {
    const { fake, inj, clipboard } = mk({ box: '你好世界' })
    const res = await applyPlan({ kind: 'delete', segId: 's1', text: '你好', tailOld: 2, tailNew: '' }, { inj, clipboard })
    assert.equal(res.action, 'deleted')
    assert.equal(res.delta, -2)
    assert.deepEqual(fake.cmds(), ['osascript', 'osascript'])
    assert.equal(fake.countOf('pbcopy'), 0)
    assert.equal(fake.state.box, '你好')
  })

  it('clipboardRestore=false 时完全不读写剪贴板之外的东西', async () => {
    const { fake, inj } = mk({ box: '你好' })
    await applyPlan(
      { kind: 'append', segId: 's1', text: '你好我们', tailNew: '我们', tailOld: 0 },
      { inj, clipboard: null, clipboardRestore: false },
    )
    assert.deepEqual(fake.cmds(), ['pbcopy', 'osascript'])
  })

  it('skip / 空文本不产生任何系统调用', async () => {
    const { fake, inj, clipboard } = mk()
    assert.equal((await applyPlan({ kind: 'skip', segId: 's1' }, { inj, clipboard })).action, 'noop')
    assert.equal((await applyPlan({ kind: 'append', segId: 's1', tailNew: '' }, { inj, clipboard })).action, 'noop')
    assert.equal(fake.calls.length, 0)
  })

  it('注入结束后剪贴板被还原成用户原内容', async () => {
    const { fake, inj, clipboard } = mk({ clipboard: '用户原来复制的' })
    await applyPlan({ kind: 'insert', segId: 's1', text: '你好', tailNew: '你好', tailOld: 0 }, { inj, clipboard })
    assert.equal(fake.state.clipboard, '你好') // 注入瞬间剪贴板里是我们的文本
    await sleep(30) // 等过还原窗口（测试里配置成 5ms）
    assert.equal(fake.state.clipboard, '用户原来复制的')
  })

  it('还原窗口内用户自己复制了东西 → 不覆盖', async () => {
    const { fake, inj, clipboard } = mk({ clipboard: '用户原来复制的' })
    await applyPlan(
      { kind: 'insert', segId: 's1', text: '你好', tailNew: '你好', tailOld: 0 },
      { inj, clipboard, },
    )
    fake.state.clipboard = '用户刚复制的新内容'
    await sleep(30)
    assert.equal(fake.state.clipboard, '用户刚复制的新内容')
  })

  it('pbcopy 失败时抛出异常（上层归类为 clipboard_failed）', async () => {
    const inj = createMacInjector({
      run: async (cmd) => {
        if (cmd === 'pbcopy') {
          const err = new Error('pbcopy 不可用')
          err.stderr = 'pbcopy: command not found'
          throw err
        }
        return { stdout: '', stderr: '' }
      },
    })
    await assert.rejects(
      () => applyPlan({ kind: 'insert', segId: 's1', text: 'x', tailNew: 'x', tailOld: 0 }, { inj, clipboard: null }),
      /pbcopy/,
    )
  })
})

describe('端到端：模拟输入框跑完文档 §3.4 的全过程', () => {
  it('中间结果上屏 + 终稿原子替换，输入框内容始终与快照一致', async () => {
    const fake = createFakeMac({ clipboard: '用户原来的剪贴板' })
    const inj = createMacInjector({ run: fake.run })
    const clipboard = createClipboardGuard({ inj, restoreDelayMs: 5 })
    const ledger = new SegmentLedger()

    const feed = async (segId, rev, text, final = false) => {
      const plan = ledger.plan({ segId, rev, text, final })
      if (!plan || plan.kind === 'skip') return plan
      const res = await applyPlan(plan, { inj, clipboard, clipboardRestore: true })
      ledger.noteInjected(res.delta)
      ledger.setTail(segId)
      ledger.commit({ segId, rev, text, final, injected: true })
      return plan
    }

    await feed('seg_1', 0, '你好')
    assert.equal(fake.state.box, '你好')
    await feed('seg_1', 0, '你好我们')
    assert.equal(fake.state.box, '你好我们')
    await feed('seg_1', 0, '你好，我们明天')
    assert.equal(fake.state.box, '你好，我们明天')
    await feed('seg_1', 1, '你好，我们明天见。', false)
    assert.equal(fake.state.box, '你好，我们明天见。')
    assert.equal(ledger.expectedPos, charLen('你好，我们明天见。'))
    assert.equal(await inj.cursorPos(), ledger.expectedPos) // 光标检查应当通过
    await feed('seg_1', 2, '你好，我们明天见吧。', true)
    assert.equal(fake.state.box, '你好，我们明天见吧。')
    assert.equal(ledger.expectedPos, charLen('你好，我们明天见吧。'))

    // 第二段接着来
    await feed('seg_2', 0, '今天天气不错')
    assert.equal(fake.state.box, '你好，我们明天见吧。今天天气不错')

    // 剪贴板最终被还原
    await sleep(30)
    assert.equal(fake.state.clipboard, '用户原来的剪贴板')
  })

  it('落定段落收到回改快照：只追加新增尾巴，绝不回删', async () => {
    const fake = createFakeMac({ box: '你好我们' })
    const inj = createMacInjector({ run: fake.run })
    const clipboard = createClipboardGuard({ inj, restoreDelayMs: 5 })
    const ledger = new SegmentLedger()
    ledger.setTail('seg_1')
    ledger.commit({ segId: 'seg_1', rev: 2, text: '你好我们', final: true, injected: true })

    const plan = ledger.plan({ segId: 'seg_1', rev: 2, text: '你好，我们明天', final: true })
    assert.equal(plan.appendOnly, true)
    assert.equal(plan.tailOld, 0)
    await applyPlan(plan, { inj, clipboard, clipboardRestore: true })
    // 光标处只会多出新增的尾巴，原有文本一个字符都没被删
    assert.equal(fake.state.box, `你好我们${plan.tailNew}`)
    assert.equal(fake.countOf('osascript') >= 1, true)
    assert.equal(fake.scripts().some((s) => s.includes('key code 51')), false)
  })

  it('emoji 与代理对不会被拆开', async () => {
    const fake = createFakeMac({ box: '表情😀好' })
    const inj = createMacInjector({ run: fake.run })
    const clipboard = createClipboardGuard({ inj, restoreDelayMs: 5 })
    const ledger = new SegmentLedger()
    ledger.setTail('seg_1')
    ledger.commit({ segId: 'seg_1', rev: 0, text: '表情😀好', injected: true })

    const plan = ledger.plan({ segId: 'seg_1', rev: 0, text: '表情😀坏', final: false })
    assert.equal(plan.tailOld, 1)
    await applyPlan(plan, { inj, clipboard, clipboardRestore: true })
    assert.equal(fake.state.box, '表情😀坏')
  })
})
