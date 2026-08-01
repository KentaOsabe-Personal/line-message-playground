import { useState, type FormEvent } from 'react'

import type { CreateChannelInput, UpdateChannelInput } from './channelAdminApi'
import type { ChannelAdminItem } from './channelAdminDto'

type Props = {
  mode: 'create' | 'edit'
  item?: ChannelAdminItem
  pending?: boolean
  onSubmit: (input: CreateChannelInput | UpdateChannelInput) => Promise<void>
  onCancel?: () => void
}

const fieldValue = (form: FormData, name: string) => String(form.get(name) ?? '')

export default function ChannelEditor({ mode, item, pending = false, onSubmit, onCancel }: Props) {
  const [message, setMessage] = useState<string | null>(null)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (pending) return
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const accessToken = fieldValue(form, 'accessToken')
    const channelSecret = fieldValue(form, 'channelSecret')
    const hasAccessToken = accessToken.trim().length > 0
    const hasChannelSecret = channelSecret.trim().length > 0
    setMessage(null)
    try {
      if (hasAccessToken !== hasChannelSecret || (mode === 'create' && !hasAccessToken)) {
        setMessage('資格情報は完全なペアで入力してください。')
        return
      }
      if (mode === 'create') {
        await onSubmit({
          label: fieldValue(form, 'label'),
          messagingApiChannelId: fieldValue(form, 'messagingApiChannelId'),
          botUserId: fieldValue(form, 'botUserId'),
          providerId: fieldValue(form, 'providerId'),
          accessToken,
          channelSecret,
          active: form.get('active') === 'on',
        })
      } else if (item !== undefined) {
        const input: UpdateChannelInput = {
          expectedUpdatedAt: item.updatedAt,
          label: fieldValue(form, 'label'),
          messagingApiChannelId: fieldValue(form, 'messagingApiChannelId'),
          botUserId: fieldValue(form, 'botUserId'),
        }
        if (item.providerId === null) input.providerId = fieldValue(form, 'providerId')
        if (hasAccessToken && hasChannelSecret) {
          input.accessToken = accessToken
          input.channelSecret = channelSecret
        }
        await onSubmit(input)
      }
    } catch {
      setMessage('入力内容を確認して、もう一度操作してください。資格情報は再入力が必要です。')
    } finally {
      formElement.reset()
    }
  }

  return (
    <form className="channel-editor" onSubmit={(event) => { void submit(event) }}>
      <h3>{mode === 'create' ? '新しいチャネルを登録' : `${item?.label ?? ''} を編集`}</h3>
      <label>運用者向け名称
        <input name="label" required maxLength={255} defaultValue={item?.label ?? ''} />
      </label>
      <label>Messaging API チャネル ID
        <input name="messagingApiChannelId" required inputMode="numeric" pattern="[0-9]{1,64}" defaultValue={item?.messagingApiChannelId ?? ''} />
      </label>
      <label>bot user ID
        <input name="botUserId" required pattern="U[0-9a-f]{32}" defaultValue={item?.botUserId ?? ''} />
      </label>
      <label>provider ID
        <input name="providerId" required inputMode="numeric" pattern="[0-9]{1,64}" defaultValue={item?.providerId ?? ''} readOnly={mode === 'edit' && item?.providerId !== null} />
      </label>
      <fieldset>
        <legend>write-only 資格情報</legend>
        {mode === 'edit' && <p>変更しない場合は両方を空欄にしてください。保存済みの値は表示しません。</p>}
        <label>チャネルアクセストークン
          <input name="accessToken" type="password" autoComplete="off" maxLength={16 * 1024} />
        </label>
        <label>チャネルシークレット
          <input name="channelSecret" type="password" autoComplete="off" maxLength={16 * 1024} />
        </label>
      </fieldset>
      {mode === 'create' && <label className="inline-field"><input name="active" type="checkbox" /> 初期状態を有効にする</label>}
      {message !== null && <p className="field-error" role="alert">{message}</p>}
      <div className="actions">
        <button type="submit" disabled={pending}>{pending ? '処理中…' : mode === 'create' ? '登録する' : '更新する'}</button>
        {onCancel !== undefined && <button type="button" className="secondary" onClick={onCancel} disabled={pending}>キャンセル</button>}
      </div>
    </form>
  )
}
