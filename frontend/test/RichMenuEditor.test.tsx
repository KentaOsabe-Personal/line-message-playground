import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import RichMenuEditor from '../src/RichMenuEditor'
import type { TemplateDescriptor } from '../src/richMenuAdminDto'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const templates: TemplateDescriptor[] = [
  {
    templateId: 'jp-link-two',
    version: 3,
    displayName: '2リンク',
    canvas: { width: 2500, height: 843 },
    areas: [
      { field: 'left', description: '左半分', bounds: { x: 0, y: 0, width: 1250, height: 843 } },
      {
        field: 'right',
        description: '右半分',
        bounds: { x: 1250, y: 0, width: 1250, height: 843 },
      },
    ],
    requiredFields: ['left', 'right'],
    limits: { displayName: 20, uri: 1000 },
  },
  {
    templateId: 'jp-link-one',
    version: 1,
    displayName: '1リンク',
    canvas: { width: 2500, height: 843 },
    areas: [
      { field: 'whole', description: '全面', bounds: { x: 0, y: 0, width: 2500, height: 843 } },
    ],
    requiredFields: ['whole'],
    limits: { displayName: 20, uri: 1000 },
  },
]

let container: HTMLDivElement
let root: Root

describe('RichMenuEditor', () => {
  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.restoreAllMocks()
  })

  // テストケース: 組み込みtemplateの全領域を表示して不正入力からpreviewを要求する。
  // 期待値: 入力上限と安全な検証理由を表示し、preview APIを呼ばない。
  test('renders every built-in field and reports safe validation errors without preview', async () => {
    const onDraftChange = vi.fn()
    const onPreview = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuEditor
          templates={templates}
          draft={null}
          onDraftChange={onDraftChange}
          onPreview={onPreview}
        />,
      ),
    )

    expect(container.textContent).toContain('2リンク')
    expect(container.textContent).toContain('版 3')
    expect(container.textContent).toContain('左半分')
    expect(container.textContent).toContain('右半分')
    expect(container.textContent).toContain('表示名は20文字以内')
    expect(container.textContent).toContain('URLは1000文字以内')
    expect(container.querySelector('input[type="file"]')).toBeNull()

    const inputs = [
      ...container.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea'),
    ]
    await act(async () => {
      inputs[0].value = '案内'
      inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
      inputs[1].value = 'http://example.com'
      inputs[1].dispatchEvent(new Event('input', { bubbles: true }))
      container
        .querySelector('form')
        ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

    expect(onPreview).not.toHaveBeenCalled()
    expect(container.textContent).toContain('left のURLは完全なHTTPS URLで入力してください。')
    expect(container.textContent).toContain('right の表示名を入力してください。')
    expect(onDraftChange).toHaveBeenCalled()
  })

  // テストケース: 未適用入力があるtemplate切替で消去確認を拒否する。
  // 期待値: 元templateと入力を維持し、previewを開始しない。
  test('keeps the original draft when template clearing is cancelled', async () => {
    const draft = {
      templateId: 'jp-link-two',
      templateVersion: 3,
      fields: {
        left: { displayName: '左', uri: 'https://example.com/left' },
        right: { displayName: '右', uri: 'https://example.com/right' },
      },
    }
    const onDraftChange = vi.fn()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await act(async () =>
      root.render(
        <RichMenuEditor
          templates={templates}
          draft={draft}
          onDraftChange={onDraftChange}
          onPreview={vi.fn()}
        />,
      ),
    )

    const select = container.querySelector('select') as HTMLSelectElement
    await act(async () => {
      select.value = 'jp-link-one:1'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })

    expect(confirm).toHaveBeenCalledWith(
      '現在の未適用入力とプレビューをすべて消去します。よろしいですか？',
    )
    expect(onDraftChange).not.toHaveBeenCalled()
    expect(
      container.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea')[0]
        .value,
    ).toBe('左')
    expect(select.value).toBe('jp-link-two:3')
  })

  // テストケース: 未適用入力があるtemplate切替で消去を承認する。
  // 期待値: 元入力を破棄し、新templateの空draftへ一度だけ切り替える。
  test('switches to an empty draft only after clearing is confirmed', async () => {
    const draft = {
      templateId: 'jp-link-two',
      templateVersion: 3,
      fields: {
        left: { displayName: '左', uri: 'https://example.com/left' },
        right: { displayName: '右', uri: 'https://example.com/right' },
      },
    }
    const onDraftChange = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await act(async () =>
      root.render(
        <RichMenuEditor
          templates={templates}
          draft={draft}
          onDraftChange={onDraftChange}
          onPreview={vi.fn()}
        />,
      ),
    )
    const select = container.querySelector('select') as HTMLSelectElement
    await act(async () => {
      select.value = 'jp-link-one:1'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(onDraftChange).toHaveBeenCalledWith({
      templateId: 'jp-link-one',
      templateVersion: 1,
      fields: { whole: { displayName: '', uri: '' } },
    })
  })
  // テストケース: 保存済みdraftと同じ版がcatalogに存在しない。
  // 期待値: 別templateの入力へfallbackせず、理由と明示的な作り直しを示す。
  test('does not replace a missing saved version with a different template', async () => {
    const onRebuild = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuEditor
          templates={templates}
          draft={{
            templateId: 'jp-link-two',
            templateVersion: 2,
            fields: { left: { displayName: '保持', uri: 'https://example.com/full?q=1#part' } },
          }}
          onDraftChange={vi.fn()}
          onPreview={vi.fn()}
          onRebuild={onRebuild}
        />,
      ),
    )
    expect(container.querySelector('input')).toBeNull()
    expect(container.textContent).toContain('同じテンプレートと版を利用できません')
    await act(async () => container.querySelector('button')?.click())
    expect(onRebuild).toHaveBeenCalledOnce()
  })
  // テストケース: 同じIDの複数版と長い保存値・項目エラーを表示する。
  // 期待値: 版を選び分け、全入力をarea順で保持し、エラーを対象欄へ関連付ける。
  test('distinguishes template versions and preserves complete values and accessible errors', async () => {
    const draft = {
      templateId: templates[0].templateId,
      templateVersion: 3,
      fields: {
        right: { displayName: '右', uri: 'https://example.com/right' },
        left: {
          displayName: '長い表示名'.repeat(10),
          uri: 'https://example.com/full?query=one&second=two#fragment',
        },
      },
    }
    const onChange = vi.fn()
    const cancel = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await act(async () =>
      root.render(
        <RichMenuEditor
          templates={[templates[0], { ...templates[0], version: 4 }]}
          draft={draft}
          onDraftChange={onChange}
          onPreview={vi.fn()}
          onCancel={cancel}
          fieldErrors={{ 'left.displayName': ['表示名を確認してください。'] }}
        />,
      ),
    )
    expect([...container.querySelectorAll('option')].map((option) => option.value)).toEqual([
      'jp-link-two:3',
      'jp-link-two:4',
    ])
    const inputs = [...container.querySelectorAll('input')]
    expect(inputs.map((input) => input.value)).toEqual([
      draft.fields.left.displayName,
      draft.fields.left.uri,
      draft.fields.right.displayName,
      draft.fields.right.uri,
    ])
    expect(container.textContent).toContain(draft.fields.left.uri)
    expect(container.textContent).toContain(draft.fields.left.displayName)
    const completeValues = [...container.querySelectorAll('fieldset details')]
    expect(completeValues).toHaveLength(2)
    for (const [index, field] of [draft.fields.left, draft.fields.right].entries()) {
      const details = completeValues[index] as HTMLDetailsElement
      expect(details.querySelector('summary')?.textContent).toBe('入力内容を全文表示')
      await act(async () => details.querySelector('summary')?.click())
      expect(details.open).toBe(true)
      expect([...details.querySelectorAll('dd')].map((item) => item.textContent)).toEqual([
        field.displayName,
        field.uri,
      ])
      expect(details.querySelector('dl')?.style.overflowWrap).toBe('anywhere')
    }
    expect(document.getElementById(inputs[0].getAttribute('aria-describedby')!)?.textContent).toBe(
      '表示名を確認してください。',
    )
    const select = container.querySelector('select')!
    await act(async () => {
      select.value = 'jp-link-two:4'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(onChange).toHaveBeenCalledWith({
      templateId: 'jp-link-two',
      templateVersion: 4,
      fields: { left: { displayName: '', uri: '' }, right: { displayName: '', uri: '' } },
    })
    await act(async () =>
      [...container.querySelectorAll('button')]
        .find((button) => button.textContent === 'キャンセル')
        ?.click(),
    )
    expect(cancel).toHaveBeenCalledOnce()
  })
  // テストケース: 保存済みdraftに必要な領域が欠けている。
  // 期待値: 空欄で補って編集を始めず、明示的な作り直しを案内する。
  test('does not invent missing saved fields', async () => {
    await act(async () =>
      root.render(
        <RichMenuEditor
          templates={templates}
          draft={{
            templateId: 'jp-link-two',
            templateVersion: 3,
            fields: { left: { displayName: '左', uri: 'https://example.com' } },
          }}
          onDraftChange={vi.fn()}
          onPreview={vi.fn()}
          onRebuild={vi.fn()}
        />,
      ),
    )
    expect(container.querySelector('input')).toBeNull()
    expect(container.textContent).toContain('全項目の設定を確認できません')
  })
})
