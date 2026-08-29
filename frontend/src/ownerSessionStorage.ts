import { parseProtectedPath } from './appRoutes'
import type { ProtectedAppPath } from './appRoutes'
import { isCanonicalUuid } from './accountDto'

export interface OwnerSessionStorage {
  saveReturnPath(path: ProtectedAppPath): void
  consumeReturnPath(): ProtectedAppPath | null
  saveDeliveryOperationId(operationId: string): void
  readDeliveryOperationId(): string | null
  clearDeliveryOperationId(): void
  setUnlinkReauthenticationPending(pending: boolean): void
  readUnlinkReauthenticationPending(): boolean
  clearAll(): void
}

const keys = Object.freeze({
  returnPath: 'line-owner:return-path',
  deliveryOperationId: 'line-owner:delivery-operation-id',
  unlinkReauthenticationPending: 'line-owner:unlink-reauthentication-pending',
})

function browserSessionStorage(): Storage | null {
  try {
    return globalThis.sessionStorage
  } catch {
    return null
  }
}

export function createOwnerSessionStorage(storage: Storage | null = browserSessionStorage()): OwnerSessionStorage {
  const read = (key: string): string | null => {
    try {
      return storage?.getItem(key) ?? null
    } catch {
      return null
    }
  }
  const remove = (key: string): void => {
    try {
      storage?.removeItem(key)
    } catch {
      // Storage denial is intentionally reduced to an empty owner session.
    }
  }
  const write = (key: string, value: string): void => {
    try {
      storage?.setItem(key, value)
    } catch {
      remove(key)
    }
  }

  return Object.freeze({
    saveReturnPath: (path: ProtectedAppPath) => {
      const validated = parseProtectedPath(path)
      if (validated === null) remove(keys.returnPath)
      else write(keys.returnPath, validated)
    },
    consumeReturnPath: () => {
      const value = read(keys.returnPath)
      remove(keys.returnPath)
      return value === null ? null : parseProtectedPath(value)
    },
    saveDeliveryOperationId: (operationId: string) => {
      if (isCanonicalUuid(operationId)) write(keys.deliveryOperationId, operationId)
      else remove(keys.deliveryOperationId)
    },
    readDeliveryOperationId: () => {
      const value = read(keys.deliveryOperationId)
      if (isCanonicalUuid(value)) return value
      if (value !== null) remove(keys.deliveryOperationId)
      return null
    },
    clearDeliveryOperationId: () => remove(keys.deliveryOperationId),
    setUnlinkReauthenticationPending: (pending: boolean) => {
      if (pending) write(keys.unlinkReauthenticationPending, '1')
      else remove(keys.unlinkReauthenticationPending)
    },
    readUnlinkReauthenticationPending: () => {
      const value = read(keys.unlinkReauthenticationPending)
      if (value === '1') return true
      if (value !== null) remove(keys.unlinkReauthenticationPending)
      return false
    },
    clearAll: () => {
      remove(keys.returnPath)
      remove(keys.deliveryOperationId)
      remove(keys.unlinkReauthenticationPending)
    },
  })
}
