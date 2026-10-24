import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import RichMenuStatePanel from '../src/RichMenuStatePanel'
import { parseCurrentMenu } from '../src/richMenuAdminDto'
import fixtures from './fixtures/rich-menu-current.json'
// テストケース: 通常と取得失敗・結果不明の状態を表示する。
// 期待値: 内部状態の一覧を出さず、短い状態と必要な案内をsemantic role付きで示す。
test('shows concise state and distinguishes unknown from success', () => {
  const parsed = parseCurrentMenu(
    fixtures.managed,
    fixtures.managed.channelId,
    fixtures.managed.channelRevision,
  )
  if (!parsed.ok) throw new Error('fixture')
  const state = parsed.value.rich
  const html = renderToStaticMarkup(<RichMenuStatePanel state={state} />)
  expect(html).toContain('適用中')
  expect(html).not.toContain('<dl>')
  expect(html).not.toContain('提供モード')
  const unknown = renderToStaticMarkup(
    <RichMenuStatePanel
      state={{ ...state, latestObservation: { ...state.latestObservation!, kind: 'unknown' } }}
    />,
  )
  expect(unknown).toContain('role="alert"')
  expect(unknown).toContain('要確認')
  expect(unknown).not.toContain('適用中')
  expect(unknown).not.toContain('未設定')
  const failed = renderToStaticMarkup(
    <RichMenuStatePanel
      current={{ state: 'failed', error: { code: 'network_error', summary: '失敗' } }}
      onRefresh={() => {}}
    />,
  )
  expect(failed).toContain('取得できません')
  expect(failed).toContain('更新')
})

// テストケース: 読取専用の利用制限と、確認済みの停止状態をそれぞれ渡す。
// 期待値: readOnlyだけでは停止を断定せず、inactiveのときだけ保存済み状態を示す。
test('distinguishes read-only restrictions from confirmed inactive channels', () => {
  const parsed = parseCurrentMenu(
    fixtures.managed,
    fixtures.managed.channelId,
    fixtures.managed.channelRevision,
  )
  if (!parsed.ok) throw new Error('fixture')
  const readOnly = renderToStaticMarkup(<RichMenuStatePanel state={parsed.value.rich} readOnly />)
  expect(readOnly).toContain('読取専用のため変更できません')
  expect(readOnly).not.toContain('停止中')
  expect(readOnly).not.toContain('保存済み管理対象')
  const inactive = renderToStaticMarkup(
    <RichMenuStatePanel
      current={{
        state: 'ready',
        value: { ...parsed.value, current: { kind: 'inactive' } },
      }}
    />,
  )
  expect(inactive).toContain('チャネルは停止中')
  expect(inactive).toContain('保存済み管理対象: あり')
  expect(inactive).not.toContain('適用中')
})

// テストケース: 既知と未知の利用不可理由がBackendから届く。
// 期待値: 安全な日本語案内だけを表示し、内部codeや未検証の文字列を露出しない。
test('translates unavailable reasons and conceals unknown internal values', () => {
  const parsed = parseCurrentMenu(
    fixtures.managed,
    fixtures.managed.channelId,
    fixtures.managed.channelRevision,
  )
  if (!parsed.ok) throw new Error('fixture')
  for (const [reason, message] of [
    ['integration_not_ready', '連携の準備が整っていないため変更できません。'],
    ['channel_inactive', 'チャネルが利用できないため変更できません。'],
    ['private_internal_canary', '現在は変更できません。更新して状態を確認してください。'],
  ]) {
    const html = renderToStaticMarkup(
      <RichMenuStatePanel state={{ ...parsed.value.rich, unavailableReason: reason }} />,
    )
    expect(html).toContain(message)
    expect(html).not.toContain(reason)
  }
})

// テストケース: 旧state propsで保存済み管理対象があるのに既定なしと観測される。
// 期待値: 未設定と断定せず、不一致を要確認として示す。
test('keeps legacy saved-resource mismatches in attention state', () => {
  const parsed = parseCurrentMenu(
    fixtures.managed,
    fixtures.managed.channelId,
    fixtures.managed.channelRevision,
  )
  if (!parsed.ok) throw new Error('fixture')
  const html = renderToStaticMarkup(
    <RichMenuStatePanel
      state={{
        ...parsed.value.rich,
        latestObservation: { ...parsed.value.rich.latestObservation!, kind: 'default_none' },
      }}
    />,
  )
  expect(html).toContain('要確認')
  expect(html).not.toContain('確認済み・未設定')
})

// テストケース: 更新中・取得失敗・更新要求で、古いstate propsも同時に渡される。
// 期待値: read状態を優先し、以前の成功表示・理由・操作候補を復活させない。
test('discards stale state whenever the current read is not ready', () => {
  const parsed = parseCurrentMenu(
    fixtures.managed,
    fixtures.managed.channelId,
    fixtures.managed.channelRevision,
  )
  if (!parsed.ok) throw new Error('fixture')
  for (const current of [
    { state: 'loading' as const, generation: 1 },
    { state: 'failed' as const, error: { code: 'network_error' as const, summary: '失敗' } },
    { state: 'refresh_required' as const, reason: 'current_changed' },
  ]) {
    const html = renderToStaticMarkup(
      <RichMenuStatePanel
        state={{ ...parsed.value.rich, unavailableReason: 'integration_not_ready' }}
        current={current}
      />,
    )
    expect(html).not.toContain('適用中')
    expect(html).not.toContain('次にできる操作')
    expect(html).not.toContain('連携の準備')
  }
})
