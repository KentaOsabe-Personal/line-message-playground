import { useMemo } from 'react'

import type { EditorDraft } from './richMenuAdminState'
import type { TemplateDescriptor } from './richMenuAdminDto'

type Props = {
  templates: TemplateDescriptor[]
  draft: EditorDraft | null
  onDraftChange: (draft: EditorDraft) => void
  onPreview: (draft: EditorDraft) => void
  onTemplateChange?: (template: TemplateDescriptor) => void
}

type FieldErrors = Record<string, { displayName?: string; uri?: string }>

const emptyFields = (template: TemplateDescriptor) => Object.fromEntries(
  template.areas.map(area => [area.field, { displayName: '', uri: '' }]),
)

function isCompleteHttpsUri(value: string): boolean {
  try {
    const uri = new URL(value)
    return uri.protocol === 'https:' && uri.hostname.length > 0 && !uri.username && !uri.password
  } catch {
    return false
  }
}

function validate(template: TemplateDescriptor, draft: EditorDraft): FieldErrors {
  const errors: FieldErrors = {}
  for (const area of template.areas) {
    const value = draft.fields[area.field] ?? { displayName: '', uri: '' }
    const fieldErrors: FieldErrors[string] = {}
    if (value.displayName.length === 0) fieldErrors.displayName = `${area.field} の表示名を入力してください。`
    else if (value.displayName.length > template.limits.displayName) fieldErrors.displayName = `${area.field} の表示名が長すぎます。`
    if (!isCompleteHttpsUri(value.uri)) fieldErrors.uri = `${area.field} のURLは完全なHTTPS URLで入力してください。`
    else if (value.uri.length > template.limits.uri) fieldErrors.uri = `${area.field} のURLが長すぎます。`
    if (Object.keys(fieldErrors).length > 0) errors[area.field] = fieldErrors
  }
  return errors
}

export default function RichMenuEditor({ templates, draft, onDraftChange, onPreview, onTemplateChange }: Props) {
  const selected = templates.find(template => template.templateId === draft?.templateId && template.version === draft.templateVersion) ?? templates[0]
  const current = useMemo<EditorDraft | null>(() => selected === undefined ? null : ({
    templateId: selected.templateId,
    templateVersion: selected.version,
    fields: draft?.templateId === selected.templateId && draft.templateVersion === selected.version ? draft.fields : emptyFields(selected),
  }), [draft, selected])
  const errors = selected === undefined || current === null ? {} : validate(selected, current)

  if (selected === undefined || current === null) return <section aria-label="リッチメニュー編集"><p>利用可能なテンプレートがありません。</p></section>

  const update = (field: string, key: 'displayName' | 'uri', value: string) => {
    onDraftChange({ ...current, fields: { ...current.fields, [field]: { ...(current.fields[field] ?? { displayName: '', uri: '' }), [key]: value } } })
  }

  return (
    <section className="rich-menu-editor" aria-labelledby="rich-menu-editor-heading">
      <div className="card-heading"><div><span className="step-number">1</span><div><p className="eyebrow">内容を入力</p><h3 id="rich-menu-editor-heading">リッチメニューを作成</h3></div></div></div>
      <label className="field-label">レイアウト
        <select value={selected.templateId} onChange={event => {
          const next = templates.find(template => template.templateId === event.target.value)
          if (next === undefined || next.templateId === selected.templateId) return
          if (draft !== null && !window.confirm('現在の未適用入力とプレビューをすべて消去します。よろしいですか？')) return
          ;(onTemplateChange ?? (template => onDraftChange({ templateId: template.templateId, templateVersion: template.version, fields: emptyFields(template) })))(next)
        }}>
          {templates.map(template => <option key={`${template.templateId}:${template.version}`} value={template.templateId}>{template.displayName}</option>)}
        </select>
      </label>
      <p className="field-help">{selected.displayName} / 版 {selected.version}・表示名 {selected.limits.displayName}文字以内 / URL {selected.limits.uri}文字以内</p>
      <form onSubmit={event => {
        event.preventDefault()
        if (Object.keys(errors).length === 0) onPreview(current)
      }} noValidate>
        <div className="rich-menu-field-list">{selected.areas.map((area, index) => {
          const value = current.fields[area.field] ?? { displayName: '', uri: '' }
          return (
            <fieldset className="rich-menu-link-field" key={area.field}>
              <legend><span>{index + 1}</span>{area.description}</legend>
              <label className="field-label">メニューに表示する文字
                <input placeholder="例：予約する" value={value.displayName} maxLength={selected.limits.displayName + 1} onInput={event => update(area.field, 'displayName', event.currentTarget.value)} />
              </label>
              {errors[area.field]?.displayName && <p role="alert">{errors[area.field].displayName}</p>}
              <label className="field-label">タップ時に開くURL
                <input type="url" placeholder="https://example.com/" value={value.uri} maxLength={selected.limits.uri + 1} onInput={event => update(area.field, 'uri', event.currentTarget.value)} />
              </label>
              {errors[area.field]?.uri && <p role="alert">{errors[area.field].uri}</p>}
            </fieldset>
          )
        })}</div>
        <div className="editor-submit"><p>{draft === null ? '入力するとプレビューを作成できます。' : '未反映の変更があります。'}</p>
        <button type="submit">プレビューを作成</button></div>
      </form>
      <p className="field-help">画像は選んだレイアウトと入力内容から自動生成されます。</p>
    </section>
  )
}
