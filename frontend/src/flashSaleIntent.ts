import type { FlashSaleIntent } from './types/flashSale'

function storageName(userId: number): string {
  return `project-one-flash-sale-intent:${userId}`
}

function positiveId(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
}

export function readFlashSaleIntent(userId: number): FlashSaleIntent | null {
  const raw = sessionStorage.getItem(storageName(userId))
  if (raw === null) return null
  const value = JSON.parse(raw) as Partial<FlashSaleIntent> | null
  if (
    !value || value.userId !== userId || !positiveId(value.itemId) ||
    !positiveId(value.addressId) || typeof value.key !== 'string' ||
    !/^flash-sale-[0-9a-f-]{36}$/.test(value.key)
  ) {
    throw new Error('保存的购买信息无法读取，请先在“我的订单”中核对已有订单。')
  }
  return Object.freeze({
    userId, key: value.key, itemId: value.itemId, addressId: value.addressId,
  })
}

export function saveFlashSaleIntent(intent: FlashSaleIntent): void {
  // 存储失败时让调用者停止发送；只在内存保存无法支持刷新后的原意图确认。
  sessionStorage.setItem(storageName(intent.userId), JSON.stringify(intent))
}

export function clearFlashSaleIntent(userId: number): void {
  sessionStorage.removeItem(storageName(userId))
}
