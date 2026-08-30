import { describe, expect, test } from 'vitest'

import { createOwnerSessionStorage } from '../src/ownerSessionStorage'

const operationId = '123e4567-e89b-42d3-a456-426614174000'

function createMemoryStorage(): Storage {
  const values = new Map<string, string>()
  return {
    get length() { return values.size },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => { values.delete(key) },
    setItem: (key, value) => { values.set(key, value) },
  }
}

describe('OwnerSessionStorage', () => {
  // テストケース: 許可済み復帰pathを保存して複数回読み取る。
  // 期待値: 最初の一回だけpathを返し、以降はnullを返す。
  test('consumes a validated return path once', () => {
    const adapter = createOwnerSessionStorage(createMemoryStorage())

    adapter.saveReturnPath('/liff/channels')

    expect(adapter.consumeReturnPath()).toBe('/liff/channels')
    expect(adapter.consumeReturnPath()).toBeNull()
  })

  // テストケース: storageへ外部URL形式の不正な復帰先が保存されている。
  // 期待値: 値を返さず、不正なstorage値も削除する。
  test('removes an invalid return path instead of exposing it', () => {
    const storage = createMemoryStorage()
    const adapter = createOwnerSessionStorage(storage)
    storage.setItem('line-owner:return-path', 'https://example.com/steal')

    expect(adapter.consumeReturnPath()).toBeNull()
    expect(storage.getItem('line-owner:return-path')).toBeNull()
  })

  // テストケース: canonicalおよび非canonicalな配信operation IDを保存する。
  // 期待値: canonical UUIDだけを保持し、不正値では既存IDも削除する。
  test('stores only canonical delivery operation IDs', () => {
    const storage = createMemoryStorage()
    const adapter = createOwnerSessionStorage(storage)

    adapter.saveDeliveryOperationId(operationId)
    expect(adapter.readDeliveryOperationId()).toBe(operationId)

    adapter.saveDeliveryOperationId(operationId.toUpperCase())
    expect(adapter.readDeliveryOperationId()).toBeNull()

    const canonicalNonV4Id = '123e4567-e89b-12d3-a456-426614174000'
    adapter.saveDeliveryOperationId(canonicalNonV4Id)
    expect(adapter.readDeliveryOperationId()).toBe(canonicalNonV4Id)
  })

  // テストケース: 全連携解除の再認証markerを設定して解除する。
  // 期待値: trueだけを保持し、false指定でmarkerを削除する。
  test('stores and clears the unlink reauthentication marker', () => {
    const adapter = createOwnerSessionStorage(createMemoryStorage())

    adapter.setUnlinkReauthenticationPending(true)
    expect(adapter.readUnlinkReauthenticationPending()).toBe(true)

    adapter.setUnlinkReauthenticationPending(false)
    expect(adapter.readUnlinkReauthenticationPending()).toBe(false)
  })

  // テストケース: owner一時情報と無関係なsession値が同じstorageに存在する。
  // 期待値: owner用3 keyだけを削除し、無関係な値を維持する。
  test('clears all owner keys without clearing unrelated session data', () => {
    const storage = createMemoryStorage()
    const adapter = createOwnerSessionStorage(storage)
    storage.setItem('unrelated', 'keep')
    adapter.saveReturnPath('/liff/account')
    adapter.saveDeliveryOperationId(operationId)
    adapter.setUnlinkReauthenticationPending(true)

    adapter.clearAll()

    expect(adapter.consumeReturnPath()).toBeNull()
    expect(adapter.readDeliveryOperationId()).toBeNull()
    expect(adapter.readUnlinkReauthenticationPending()).toBe(false)
    expect(storage.getItem('unrelated')).toBe('keep')
  })

  // テストケース: browser storageの全操作がSecurityErrorになる。
  // 期待値: 例外を外へ出さず、安全な空状態へ収束する。
  test('fails closed when browser storage is unavailable', () => {
    const unavailable = new Proxy({} as Storage, {
      get: () => { throw new DOMException('blocked', 'SecurityError') },
    })
    const adapter = createOwnerSessionStorage(unavailable)

    expect(() => adapter.saveReturnPath('/liff')).not.toThrow()
    expect(adapter.consumeReturnPath()).toBeNull()
    expect(() => adapter.saveDeliveryOperationId(operationId)).not.toThrow()
    expect(adapter.readDeliveryOperationId()).toBeNull()
    expect(() => adapter.setUnlinkReauthenticationPending(true)).not.toThrow()
    expect(adapter.readUnlinkReauthenticationPending()).toBe(false)
    expect(() => adapter.clearAll()).not.toThrow()
  })

  // テストケース: 旧owner値があり、storageの書込みだけが失敗する。
  // 期待値: 旧値を残さず、対象keyを安全な空状態へ縮約する。
  test('removes stale owner values when only storage writes fail', () => {
    const storage = createMemoryStorage()
    storage.setItem('line-owner:return-path', '/liff/account')
    storage.setItem('line-owner:delivery-operation-id', operationId)
    storage.setItem('line-owner:unlink-reauthentication-pending', '1')
    const writeBlockedStorage = new Proxy(storage, {
      get: (target, property) => property === 'setItem'
        ? () => { throw new DOMException('full', 'QuotaExceededError') }
        : Reflect.get(target, property),
    })
    const adapter = createOwnerSessionStorage(writeBlockedStorage)

    adapter.saveReturnPath('/liff/channels')
    adapter.saveDeliveryOperationId('223e4567-e89b-42d3-a456-426614174000')
    adapter.setUnlinkReauthenticationPending(true)

    expect(adapter.consumeReturnPath()).toBeNull()
    expect(adapter.readDeliveryOperationId()).toBeNull()
    expect(adapter.readUnlinkReauthenticationPending()).toBe(false)
  })

  // テストケース: adapterが全種類のowner一時情報を保存する。
  // 期待値: 許可された3 keyだけを使い、本文や資格情報を保存しない。
  test('uses only the three allowlisted keys and never stores owner content', () => {
    const storage = createMemoryStorage()
    const adapter = createOwnerSessionStorage(storage)
    adapter.saveReturnPath('/liff/deliveries')
    adapter.saveDeliveryOperationId(operationId)
    adapter.setUnlinkReauthenticationPending(true)

    expect(Array.from({ length: storage.length }, (_, index) => storage.key(index))).toEqual([
      'line-owner:return-path',
      'line-owner:delivery-operation-id',
      'line-owner:unlink-reauthentication-pending',
    ])
  })
})
