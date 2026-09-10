/**
 * segments.test.js —— 段落账本与替换算法单测
 * 覆盖 docs/voice-input/桌面客户端.md §9 表格里的全部用例，以及边界情况。
 * 运行：node --test
 */
import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFER_MAX_MS,
  SegmentLedger,
  canReplace,
  charLen,
  commonPrefixLen,
  describePlan,
  diffTail,
  gateAction,
} from '../src/segments.js'

/** 造一个「已注入」的账目 */
function withInjected(ledger, segId, text, { rev = 1, final = false } = {}) {
  ledger.plan({ segId, rev: 0, text: '', final: false })
  ledger.setTail(segId)
  ledger.commit({ segId, rev, text, final, injected: true })
  return ledger
}

describe('charLen / commonPrefixLen / diffTail', () => {
  it('charLen 按 Unicode 字符计数（emoji 算 1 个）', () => {
    assert.equal(charLen('你好，我们明天见。'), 9)
    assert.equal(charLen('😀'), 1)
    assert.equal(charLen('👨‍👩‍👧'), 5) // ZWJ 序列按码点计（文档只要求不拆代理对）
    assert.equal(charLen(''), 0)
    assert.equal(charLen(undefined), 0)
  })

  it('commonPrefixLen 正确处理 emoji / 代理对', () => {
    assert.equal(commonPrefixLen('表情😀好', '表情😀好呀'), 4)
    assert.equal(commonPrefixLen('abc', 'abd'), 2)
    assert.equal(commonPrefixLen('', 'abc'), 0)
    assert.equal(commonPrefixLen('abc', 'abc'), 3)
    // 代理对不会被拆成半个
    assert.equal(commonPrefixLen('😀a', '😀b'), 1)
  })

  it('diffTail 给出最小尾部操作', () => {
    assert.deepEqual(diffTail('你好', '你好我们'), {
      prefix: '你好',
      tailOld: 0,
      tailNew: '我们',
      prevChars: 2,
      nextChars: 4,
    })
    assert.deepEqual(diffTail('你好我们', '你好，我们明天'), {
      prefix: '你好',
      tailOld: 2,
      tailNew: '，我们明天',
      prevChars: 4,
      nextChars: 7,
    })
    assert.deepEqual(diffTail('abc', 'abc'), {
      prefix: 'abc',
      tailOld: 0,
      tailNew: '',
      prevChars: 3,
      nextChars: 3,
    })
  })
})

describe('§9 必测用例表', () => {
  it('① 你好 → 你好我们：纯追加「我们」', () => {
    const ledger = withInjected(new SegmentLedger(), 'seg_1', '你好')
    const plan = ledger.plan({ segId: 'seg_1', rev: 2, text: '你好我们', final: false })
    assert.equal(plan.kind, 'append')
    assert.equal(plan.tailOld, 0)
    assert.equal(plan.tailNew, '我们')
    assert.equal(plan.appendOnly, false)
  })

  it('② 你好我们 → 你好，我们明天：tailOld=2, tailNew=「，我们明天」', () => {
    const ledger = withInjected(new SegmentLedger(), 'seg_1', '你好我们')
    const plan = ledger.plan({ segId: 'seg_1', rev: 2, text: '你好，我们明天', final: false })
    assert.equal(plan.kind, 'replace')
    assert.equal(plan.tailOld, 2)
    assert.equal(plan.tailNew, '，我们明天')
    assert.equal(plan.prefix, '你好')
  })

  it('③ 你好，我们明天见。 → 同文本：无操作', () => {
    const ledger = withInjected(new SegmentLedger(), 'seg_1', '你好，我们明天见。')
    const plan = ledger.plan({ segId: 'seg_1', rev: 3, text: '你好，我们明天见。', final: true })
    assert.equal(plan.kind, 'skip')
    assert.equal(plan.reason, 'unchanged')
  })

  it('④ abc → abc 但 rev 更小：忽略（幂等）', () => {
    const ledger = withInjected(new SegmentLedger(), 'seg_1', 'abc', { rev: 2 })
    assert.equal(ledger.plan({ segId: 'seg_1', rev: 1, text: 'abc' }), null)
    assert.equal(ledger.plan({ segId: 'seg_1', rev: 0, text: 'abcd' }), null)
    // 新版本才处理
    assert.equal(ledger.plan({ segId: 'seg_1', rev: 3, text: 'abcd' }).kind, 'append')
  })

  it('④b rev 是状态档位不是版本号：同一 rev 的中间结果必须继续处理', () => {
    // 后端 _on_partial 每 ≥120ms 下发一次 rev=0，文本不断增长
    const ledger = new SegmentLedger()
    ledger.commit({ segId: 'seg_1', rev: 0, text: '你好', final: false, injected: true })
    const plan = ledger.plan({ segId: 'seg_1', rev: 0, text: '你好我们', final: false })
    assert.equal(plan.kind, 'append')
    assert.equal(plan.tailNew, '我们')
    // 同 rev 同文本 → 无操作（真正的幂等靠文本比对）
    ledger.commit({ segId: 'seg_1', rev: 0, text: '你好我们', final: false, injected: true })
    assert.equal(ledger.plan({ segId: 'seg_1', rev: 0, text: '你好我们', final: false }).kind, 'skip')
  })

  it('⑤ 表情😀好 → 表情😀好呀：tailOld=0, tailNew=「呀」（emoji 不被拆）', () => {
    const ledger = withInjected(new SegmentLedger(), 'seg_1', '表情😀好')
    const plan = ledger.plan({ segId: 'seg_1', rev: 2, text: '表情😀好呀', final: false })
    assert.equal(plan.kind, 'append')
    assert.equal(plan.tailOld, 0)
    assert.equal(plan.tailNew, '呀')
    assert.equal(charLen(plan.text), 5)
  })

  it('⑤b emoji 位于差异处：尾部替换不破坏代理对', () => {
    const ledger = withInjected(new SegmentLedger(), 'seg_1', '表情😀好')
    const plan = ledger.plan({ segId: 'seg_1', rev: 2, text: '表情😀坏', final: false })
    assert.equal(plan.kind, 'replace')
    assert.equal(plan.tailOld, 1)
    assert.equal(plan.tailNew, '坏')
  })

  it('⑥ final 段落再收到 partial：只做正向追加，不回改', () => {
    const ledger = new SegmentLedger()
    withInjected(ledger, 'seg_1', '你好我们', { rev: 2, final: true })
    const plan = ledger.plan({ segId: 'seg_1', rev: 3, text: '你好，我们明天', final: false })
    assert.equal(plan.kind, 'append')
    assert.equal(plan.appendOnly, true)
    assert.equal(plan.tailOld, 0) // 绝不回改
    assert.equal(plan.tailNew, '，我们明天')
    assert.equal(plan.blocked, 'committed')
  })

  it('⑥b final 段落收到需要回删的快照：不动作', () => {
    const ledger = new SegmentLedger()
    withInjected(ledger, 'seg_1', '你好我们', { rev: 2, final: true })
    const plan = ledger.plan({ segId: 'seg_1', rev: 3, text: '你好', final: false })
    assert.equal(plan.kind, 'skip')
    assert.equal(plan.reason, 'committed')
  })

  it('⑦ abandonAll 后收到需要回改的快照：只追加，返回 abandoned', () => {
    const ledger = new SegmentLedger()
    withInjected(ledger, 'seg_1', '你好我们')
    const abandoned = ledger.abandonAll('user_typed')
    assert.deepEqual(abandoned, [{ segId: 'seg_1', reason: 'user_typed' }])
    const plan = ledger.plan({ segId: 'seg_1', rev: 3, text: '你好，我们明天', final: false })
    assert.equal(plan.kind, 'append')
    assert.equal(plan.appendOnly, true)
    assert.equal(plan.tailOld, 0)
  })
})

describe('账本状态迁移', () => {
  it('新段落 → insert，整段文本', () => {
    const ledger = new SegmentLedger()
    const plan = ledger.plan({ segId: 'seg_9', rev: 0, text: '你好', final: false })
    assert.equal(plan.kind, 'insert')
    assert.equal(plan.tailNew, '你好')
    assert.equal(plan.chars, 2)
    assert.equal(plan.isTail, true)
  })

  it('插入模式关闭时只记账（injected=false），之后仍按整段插入', () => {
    const ledger = new SegmentLedger()
    ledger.markPending({ segId: 'seg_1', rev: 1, text: '你好', final: false })
    assert.equal(ledger.get('seg_1').injected, false)
    const plan = ledger.plan({ segId: 'seg_1', rev: 2, text: '你好我们', final: false })
    assert.equal(plan.kind, 'insert')
    assert.equal(plan.tailNew, '你好我们') // 什么都没注入过 → 整段插入
  })

  it('已注入的段落不会被重新排进「待插入」（否则重开插入模式会重复上屏）', () => {
    const ledger = new SegmentLedger()
    withInjected(ledger, 'seg_1', '你好我们')
    // 用户中途关掉插入模式，此时又来了一帧快照 → 只更新元数据
    ledger.touch('seg_1', { rev: 2, text: '你好，我们', final: false })
    assert.equal(ledger.get('seg_1').injected, true)
    assert.equal(ledger.lastPending(), null)
  })

  it('markHistory：历史段落只登记，不会被当成待插入', () => {
    const ledger = new SegmentLedger()
    ledger.markHistory({ segId: 'seg_old', rev: 2, text: '历史句子', seq: 1 })
    assert.equal(ledger.lastPending(), null)
    assert.equal(ledger.expectedPos, 0) // 历史段落不计入光标预期
    assert.equal(ledger.get('seg_old').committed, true)
  })

  it('纯回删：tailNew 为空 → delete', () => {
    const ledger = withInjected(new SegmentLedger(), 'seg_1', '你好世界')
    const plan = ledger.plan({ segId: 'seg_1', rev: 2, text: '你好' })
    assert.equal(plan.kind, 'delete')
    assert.equal(plan.tailOld, 2)
    assert.equal(plan.tailNew, '')
  })

  it('expectedPos 随注入字符数累加 / 回删递减', () => {
    const ledger = new SegmentLedger()
    assert.equal(ledger.expectedPos, 0)
    ledger.noteInjected(4)
    assert.equal(ledger.expectedPos, 4)
    ledger.noteInjected(-2)
    assert.equal(ledger.expectedPos, 2)
    ledger.noteInjected(-10)
    assert.equal(ledger.expectedPos, 0) // 不为负
    ledger.invalidateCursor()
    assert.equal(ledger.expectedPos, null)
    assert.equal(ledger.modeDirty, true)
  })

  it('rebaselineCursor：用实测光标位置重新基线化，并让光标检查重新生效', () => {
    const ledger = new SegmentLedger()
    ledger.invalidateCursor()
    assert.equal(ledger.expectedPos, null)
    assert.equal(ledger.modeDirty, true)
    ledger.rebaselineCursor(42)
    assert.equal(ledger.expectedPos, 42)
    assert.equal(ledger.modeDirty, false)
    // 取不到位置时保持原状
    ledger.rebaselineCursor(-1)
    assert.equal(ledger.expectedPos, 42)
  })

  it('freezeOthers：新段开始后，旧段永久失去回改资格', () => {
    const ledger = new SegmentLedger()
    withInjected(ledger, 'seg_1', '你好我们')
    const frozen = ledger.freezeOthers('seg_2')
    ledger.setTail('seg_2') // index.js 在插入新段前会立刻切换尾部
    assert.deepEqual(frozen, ['seg_1'])
    const plan = ledger.plan({ segId: 'seg_1', rev: 5, text: '你好，我们明天' })
    assert.equal(plan.kind, 'append')
    assert.equal(plan.appendOnly, true)
    assert.equal(plan.tailOld, 0) // 不回改
    assert.equal(plan.isTail, false) // 已被新段顶掉，调用方必须跳过注入
  })

  it('isTail：被新段顶掉之后不再是尾部', () => {
    const ledger = new SegmentLedger()
    withInjected(ledger, 'seg_1', '你好')
    ledger.setTail('seg_2')
    assert.equal(ledger.isTail('seg_1'), false)
    assert.equal(ledger.isTail('seg_2'), true)
    assert.equal(ledger.plan({ segId: 'seg_2', rev: 1, text: '你好世界' }).isTail, true)
  })

  it('lastPending 返回最近一条未注入的段落', () => {
    const ledger = new SegmentLedger()
    ledger.markPending({ segId: 'seg_1', rev: 1, text: '第一句', final: true })
    ledger.markPending({ segId: 'seg_2', rev: 1, text: '第二句', final: true })
    assert.equal(ledger.lastPending().segId, 'seg_2')
    ledger.commit({ segId: 'seg_2', rev: 1, text: '第二句', final: true, injected: true })
    assert.equal(ledger.lastPending().segId, 'seg_1')
  })

  it('stats / clear / forget', () => {
    const ledger = new SegmentLedger()
    withInjected(ledger, 'seg_1', '你好')
    ledger.markPending({ segId: 'seg_2', rev: 1, text: '第二句', final: true })
    const s = ledger.stats()
    assert.equal(s.total, 2)
    assert.equal(s.injected, 1)
    assert.equal(s.pending, 1)
    assert.equal(s.committed, 1)
    assert.equal(s.open, 1)
    assert.equal(s.tailSegId, 'seg_1')
    ledger.forget('seg_1')
    assert.equal(ledger.stats().tailSegId, null)
    ledger.clear()
    assert.equal(ledger.size, 0)
    assert.equal(ledger.expectedPos, 0)
  })

  it('超量时淘汰最旧账目，且不动尾部段', () => {
    const ledger = new SegmentLedger({ maxSegments: 3 })
    for (let i = 1; i <= 5; i += 1) {
      withInjected(ledger, `seg_${i}`, `第${i}句`)
    }
    assert.ok(ledger.size <= 4)
    assert.ok(ledger.has('seg_5'))
  })
})

describe('三道闸 canReplace', () => {
  const base = {
    replaceEnabled: true,
    insertMode: true,
    roomBusy: false,
    msSinceLastInject: 99999,
    commitDelayMs: 600,
    isTail: true,
    modeDirty: false,
    expectedPos: 10,
    actualCursorPos: 10,
  }

  it('全部通过 → null', () => {
    assert.equal(canReplace(base), null)
  })

  it('replaceEnabled=false → disabled（不可延后）', () => {
    assert.equal(canReplace({ ...base, replaceEnabled: false }), 'disabled')
    assert.equal(gateAction('disabled'), 'abandon')
  })

  it('插入模式关闭 → mode_off（不可延后）', () => {
    assert.equal(canReplace({ ...base, insertMode: false }), 'mode_off')
    assert.equal(gateAction('mode_off'), 'abandon')
  })

  it('房间还在录音 → room_busy（延后重试）', () => {
    assert.equal(canReplace({ ...base, roomBusy: true }), 'room_busy')
    assert.equal(gateAction('room_busy'), 'defer')
    assert.ok(DEFER_MAX_MS.room_busy > 0)
  })

  it('距上次注入太近 → too_soon（延后重试）', () => {
    assert.equal(canReplace({ ...base, msSinceLastInject: 100 }), 'too_soon')
    assert.equal(gateAction('too_soon'), 'defer')
    assert.equal(canReplace({ ...base, msSinceLastInject: 600 }), null)
  })

  it('光标位置取不到（-1）→ 跳过检查', () => {
    assert.equal(canReplace({ ...base, actualCursorPos: -1 }), null)
    assert.equal(canReplace({ ...base, expectedPos: null, actualCursorPos: 3 }), null)
  })

  it('光标位置不符 → cursor_mismatch', () => {
    assert.equal(canReplace({ ...base, actualCursorPos: 8 }), 'cursor_mismatch')
    assert.equal(gateAction('cursor_mismatch'), 'abandon')
  })

  it('已放弃替换权（modeDirty）→ 不再做光标检查', () => {
    assert.equal(canReplace({ ...base, modeDirty: true, actualCursorPos: 1 }), null)
  })

  it('段落不在光标处（被后续段落顶掉）→ cursor_mismatch', () => {
    assert.equal(canReplace({ ...base, isTail: false }), 'cursor_mismatch')
  })

  it('闸门优先级：配置 > 模式 > 尾部 > 房间 > 时间 > 光标', () => {
    const bad = {
      replaceEnabled: false,
      insertMode: false,
      isTail: false,
      roomBusy: true,
      msSinceLastInject: 0,
      commitDelayMs: 600,
      modeDirty: false,
      expectedPos: 1,
      actualCursorPos: 99,
    }
    assert.equal(canReplace(bad), 'disabled')
    assert.equal(canReplace({ ...bad, replaceEnabled: true }), 'mode_off')
    assert.equal(canReplace({ ...bad, replaceEnabled: true, insertMode: true }), 'cursor_mismatch')
    assert.equal(
      canReplace({ ...bad, replaceEnabled: true, insertMode: true, isTail: true }),
      'room_busy',
    )
    assert.equal(
      canReplace({ ...bad, replaceEnabled: true, insertMode: true, isTail: true, roomBusy: false }),
      'too_soon',
    )
  })
})

describe('describePlan', () => {
  it('渲染中文动作描述', () => {
    assert.equal(describePlan(null), '无操作')
    assert.equal(describePlan({ kind: 'insert', text: '你好' }), '插入「你好」')
    assert.equal(describePlan({ kind: 'append', tailNew: '我们' }), '追加「我们」')
    assert.equal(describePlan({ kind: 'replace', tailNew: '，我们' }), '替换尾部「，我们」')
    assert.equal(describePlan({ kind: 'delete', tailOld: 2 }), '回删 2 字')
  })
})

describe('文档 §3.4 示例时序（你好我们明天见）', () => {
  it('t0~t5 的动作与输入框内容一致', () => {
    const ledger = new SegmentLedger()
    let box = '' // 模拟输入框
    const apply = (plan) => {
      if (!plan || plan.kind === 'skip') return
      if (plan.kind === 'insert' || plan.kind === 'append') {
        box += plan.tailNew
      } else if (plan.kind === 'replace') {
        box = box.slice(0, box.length - plan.tailOld) + plan.tailNew
      } else if (plan.kind === 'delete') {
        box = box.slice(0, box.length - plan.tailOld)
      }
      ledger.setTail(plan.segId)
      ledger.commit({ segId: plan.segId, rev: plan.rev, text: plan.text, final: plan.final, injected: true })
    }

    apply(ledger.plan({ segId: 'seg_1', rev: 0, text: '你好', final: false }))
    assert.equal(box, '你好')

    apply(ledger.plan({ segId: 'seg_1', rev: 0, text: '你好我们', final: false }))
    assert.equal(box, '你好我们')

    apply(ledger.plan({ segId: 'seg_1', rev: 0, text: '你好，我们明天', final: false }))
    assert.equal(box, '你好，我们明天')

    apply(ledger.plan({ segId: 'seg_1', rev: 1, text: '你好，我们明天见。', final: false }))
    assert.equal(box, '你好，我们明天见。')

    apply(ledger.plan({ segId: 'seg_1', rev: 2, text: '你好，我们明天见。', final: true }))
    assert.equal(box, '你好，我们明天见。') // 润色无改动 → 不变

    apply(ledger.plan({ segId: 'seg_1', rev: 2, text: '你好，我们明天见吧。', final: true }))
    assert.equal(box, '你好，我们明天见吧。') // 原子替换
  })

  it('多段连续说话：第二段接在第一段后面', () => {
    const ledger = new SegmentLedger()
    let box = ''
    const run = (segId, rev, text, final = false) => {
      const plan = ledger.plan({ segId, rev, text, final })
      if (!plan || plan.kind === 'skip') return plan
      if (plan.kind === 'insert') {
        ledger.freezeOthers(segId)
        box += plan.tailNew
      } else if (plan.kind === 'append') {
        box += plan.tailNew
      } else if (plan.kind === 'replace') {
        box = box.slice(0, box.length - plan.tailOld) + plan.tailNew
      }
      ledger.setTail(segId)
      ledger.commit({ segId, rev, text, final, injected: true })
      return plan
    }
    run('seg_1', 0, '你好')
    run('seg_1', 2, '你好，我们明天见。', true)
    run('seg_2', 0, '今天天气不错')
    assert.equal(box, '你好，我们明天见。今天天气不错')
    // 第一段已落定（且已被第二段顶掉），之后的修正只能追加、且已不在光标处
    const late = run('seg_1', 2, '你好，我们明天见吧。', true)
    assert.equal(late.kind, 'append')
    assert.equal(late.appendOnly, true)
    assert.equal(late.isTail, false)
  })
})
