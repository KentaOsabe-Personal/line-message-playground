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
    <section aria-labelledby="rich-menu-editor-heading">
      <h3 id="rich-menu-editor-heading">テンプレート編集</h3>
      <label>組み込みテンプレート
        <select value={selected.templateId} onChange={event => {
          const next = templates.find(template => template.templateId === event.target.value)
          if (next === undefined || next.templateId === selected.templateId) return
          if (draft !== null && !window.confirm('現在の未適用入力とプレビューをすべて消去します。よろしいですか？')) return
          ;(onTemplateChange ?? (template => onDraftChange({ templateId: template.templateId, templateVersion: template.version, fields: emptyFields(template) })))(next)
        }}>
          {templates.map(template => <option key={`${template.templateId}:${template.version}`} value={template.templateId}>{template.displayName}</option>)}
        </select>
      </label>
      <p>{selected.displayName} / 版 {selected.version}</p>
      <p>表示名 {selected.limits.displayName}文字以内 / URL {selected.limits.uri}文字以内</p>
      <form onSubmit={event => {
        event.preventDefault()
        if (Object.keys(errors).length === 0) onPreview(current)
      }} noValidate>
        {selected.areas.map(area => {
          const value = current.fields[area.field] ?? { displayName: '', uri: '' }
          return (
            <fieldset key={area.field}>
              <legend>{area.description}（{area.bounds.x}, {area.bounds.y}, {area.bounds.width}×{area.bounds.height}）</legend>
              <label>表示名
                <input value={value.displayName} maxLength={selected.limits.displayName + 1} onInput={event => update(area.field, 'displayName', event.currentTarget.value)} />
              </label>
              {errors[area.field]?.displayName && <p role="alert">{errors[area.field].displayName}</p>}
              <label>完全なHTTPS URL
                <input type="url" value={value.uri} maxLength={selected.limits.uri + 1} onInput={event => update(area.field, 'uri', event.currentTarget.value)} />
              </label>
              {errors[area.field]?.uri && <p role="alert">{errors[area.field].uri}</p>}
            </fieldset>
          )
        })}
        <p>{draft === null ? '未適用入力はありません。' : '未適用の変更があります。'}</p>
        <button type="submit">プレビューを生成</button>
      </form>
      <p>組み込みテンプレートの表示名とURIリンクだけを編集できます。</p>
    </section>
  )
}
