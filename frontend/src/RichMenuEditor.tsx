import { useId, useMemo, useState } from 'react'

import type { EditorDraft } from './richMenuAdminState'
import type { TemplateDescriptor } from './richMenuAdminDto'

type Props = {
  editing?: boolean
  templates: TemplateDescriptor[]
  draft: EditorDraft | null
  onDraftChange: (draft: EditorDraft) => void
  onPreview: (draft: EditorDraft) => void
  onRebuild?: () => void
  onCancel?: () => void
  busy?: boolean
  fieldErrors?: Record<string, string[]>
  onTemplateChange?: (template: TemplateDescriptor) => void
}

type FieldErrors = Record<string, { displayName?: string; uri?: string }>

const emptyFields = (template: TemplateDescriptor) =>
  Object.fromEntries(template.areas.map((area) => [area.field, { displayName: '', uri: '' }]))

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
    if (value.displayName.length === 0)
      fieldErrors.displayName = `${area.field} の表示名を入力してください。`
    else if (value.displayName.length > template.limits.displayName)
      fieldErrors.displayName = `${area.field} の表示名が長すぎます。`
    if (!isCompleteHttpsUri(value.uri))
      fieldErrors.uri = `${area.field} のURLは完全なHTTPS URLで入力してください。`
    else if (value.uri.length > template.limits.uri)
      fieldErrors.uri = `${area.field} のURLが長すぎます。`
    if (Object.keys(fieldErrors).length > 0) errors[area.field] = fieldErrors
  }
  return errors
}

export default function RichMenuEditor({
  editing = false,
  templates,
  draft,
  onDraftChange,
  onPreview,
  onTemplateChange,
  onRebuild,
  onCancel,
  busy = false,
  fieldErrors = {},
}: Props) {
  const id = useId()
  const [validationVisible, setValidationVisible] = useState(false)
  const selected =
    templates.find(
      (template) =>
        template.templateId === draft?.templateId && template.version === draft.templateVersion,
    ) ?? (draft !== null ? undefined : templates[0])
  const current = useMemo<EditorDraft | null>(
    () =>
      selected === undefined ||
      (draft !== null &&
        (Object.keys(draft.fields).length !== selected.areas.length ||
          selected.areas.some((area) => !(area.field in draft.fields))))
        ? null
        : {
            templateId: selected.templateId,
            templateVersion: selected.version,
            fields:
              draft?.templateId === selected.templateId &&
              draft.templateVersion === selected.version
                ? draft.fields
                : emptyFields(selected),
          },
    [draft, selected],
  )
  const errors = selected === undefined || current === null ? {} : validate(selected, current)

  if (selected === undefined || current === null)
    return (
      <section aria-label="リッチメニュー編集">
        <p role="alert">
          {draft === null
            ? '利用可能なテンプレートがありません。'
            : '同じテンプレートと版を利用できません、または全項目の設定を確認できません。保存済みの入力は変更していません。'}
        </p>
        {onRebuild && (
          <button type="button" onClick={onRebuild} disabled={busy}>
            別のテンプレートで作り直す
          </button>
        )}
        {onCancel && (
          <button type="button" onClick={onCancel}>
            キャンセル
          </button>
        )}
      </section>
    )

  const update = (field: string, key: 'displayName' | 'uri', value: string) => {
    onDraftChange({
      ...current,
      fields: {
        ...current.fields,
        [field]: { ...(current.fields[field] ?? { displayName: '', uri: '' }), [key]: value },
      },
    })
  }

  return (
    <section className="rich-menu-editor" aria-labelledby="rich-menu-editor-heading">
      <header className="rich-menu-editor-heading">
        <h3 id="rich-menu-editor-heading" tabIndex={-1}>
          {editing ? 'メニューを編集' : 'メニューを作成'}
        </h3>
        <p>変更はまだ適用されていません。現在のメニューは、LINEに反映するまで変わりません。</p>
      </header>
      <label className="field-label">
        テンプレート
        <select
          value={`${selected.templateId}:${selected.version}`}
          disabled={busy}
          onChange={(event) => {
            const next = templates.find(
              (template) => `${template.templateId}:${template.version}` === event.target.value,
            )
            if (
              next === undefined ||
              (next.templateId === selected.templateId && next.version === selected.version)
            )
              return
            if (
              Object.values(current.fields).some(
                (field) => field.displayName !== '' || field.uri !== '',
              ) &&
              !window.confirm('現在の未適用入力とプレビューをすべて消去します。よろしいですか？')
            )
              return
            setValidationVisible(false)
            ;(
              onTemplateChange ??
              ((template) =>
                onDraftChange({
                  templateId: template.templateId,
                  templateVersion: template.version,
                  fields: emptyFields(template),
                }))
            )(next)
          }}
        >
          {templates.map((template) => (
            <option
              key={`${template.templateId}:${template.version}`}
              value={`${template.templateId}:${template.version}`}
            >
              {template.displayName}（版 {template.version}）
            </option>
          ))}
        </select>
      </label>
      <p className="field-help">
        テンプレートを変更すると、確認後に全項目の入力をクリアします。表示名は
        {selected.limits.displayName}文字以内、URLは{selected.limits.uri}
        文字以内で入力してください。
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (busy) return
          if (Object.keys(errors).length === 0) onPreview(current)
          else setValidationVisible(true)
        }}
        noValidate
      >
        <h4>表示名とリンク先</h4>
        <div className="rich-menu-field-list">
          {selected.areas.map((area, index) => {
            const value = current.fields[area.field] ?? { displayName: '', uri: '' }
            return (
              <fieldset className="rich-menu-link-field rich-menu-edit-row" key={area.field}>
                <legend className="visually-hidden">
                  <span>{index + 1}</span>
                  {area.description}
                </legend>
                <div className="rich-menu-edit-control">
                  <label className="field-label">
                    表示名
                    <input
                      disabled={busy}
                      aria-invalid={Boolean(
                        (validationVisible && errors[area.field]?.displayName) ||
                        fieldErrors[`${area.field}.displayName`],
                      )}
                      aria-describedby={`${id}-${area.field}-name-error`}
                      placeholder="例：予約する"
                      value={value.displayName}
                      maxLength={selected.limits.displayName + 1}
                      onInput={(event) =>
                        update(area.field, 'displayName', event.currentTarget.value)
                      }
                    />
                  </label>
                  {validationVisible &&
                    errors[area.field]?.displayName &&
                    !fieldErrors[`${area.field}.displayName`] && (
                      <p id={`${id}-${area.field}-name-error`} role="alert">
                        {errors[area.field].displayName}
                      </p>
                    )}
                </div>
                <div className="rich-menu-edit-control">
                  <label className="field-label">
                    リンク先URL
                    <input
                      disabled={busy}
                      aria-invalid={Boolean(
                        (validationVisible && errors[area.field]?.uri) ||
                        fieldErrors[`${area.field}.uri`],
                      )}
                      aria-describedby={`${id}-${area.field}-uri-error`}
                      type="url"
                      placeholder="https://example.com/"
                      value={value.uri}
                      maxLength={selected.limits.uri + 1}
                      onInput={(event) => update(area.field, 'uri', event.currentTarget.value)}
                    />
                  </label>
                  {validationVisible &&
                    errors[area.field]?.uri &&
                    !fieldErrors[`${area.field}.uri`] && (
                      <p id={`${id}-${area.field}-uri-error`} role="alert">
                        {errors[area.field].uri}
                      </p>
                    )}
                </div>
                <details className="rich-menu-complete-values">
                  <summary>入力内容を全文表示</summary>
                  <dl style={{ overflowWrap: 'anywhere' }}>
                    <dt>表示名</dt>
                    <dd>{value.displayName}</dd>
                    <dt>リンク先URL</dt>
                    <dd>{value.uri}</dd>
                  </dl>
                </details>
                {(['displayName', 'uri'] as const).map(
                  (key) =>
                    fieldErrors[`${area.field}.${key}`] && (
                      <p
                        key={key}
                        id={`${id}-${area.field}-${key === 'displayName' ? 'name' : 'uri'}-error`}
                        role="alert"
                      >
                        {fieldErrors[`${area.field}.${key}`].join(' ')}
                      </p>
                    ),
                )}
              </fieldset>
            )
          })}
        </div>
        <div className="editor-submit rich-menu-edit-actions">
          <button type="submit" disabled={busy}>
            変更内容をプレビュー
          </button>
          {onCancel && (
            <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
              キャンセル
            </button>
          )}
        </div>
        <p className="field-help">キャンセルすると、未適用の入力内容とプレビューを破棄します。</p>
      </form>
      <p className="field-help">画像は選んだレイアウトと入力内容から自動生成されます。</p>
    </section>
  )
}
