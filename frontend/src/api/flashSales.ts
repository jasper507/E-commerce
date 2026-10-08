import { ensureCsrfCookie } from './auth'
import type { FlashSaleIntent, FlashSaleItem } from '../types/flashSale'
import type { Order } from '../types/order'

export class FlashSaleApiError extends Error {
  readonly status: number
  readonly code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = 'FlashSaleApiError'
    this.status = status
    this.code = code
  }
}

async function rejectResponse(response: Response): Promise<never> {
  const payload = await response.json() as Record<string, unknown>
  throw new FlashSaleApiError(
    response.status,
    typeof payload.detail === 'string' ? payload.detail : `请求失败（HTTP ${response.status}）`,
    typeof payload.code === 'string' ? payload.code : undefined,
  )
}

export async function fetchFlashSaleItems(): Promise<FlashSaleItem[]> {
  const response = await fetch('/api/flash-sales/items/', {
    credentials: 'same-origin', headers: { Accept: 'application/json' },
  })
  if (!response.ok) return rejectResponse(response)
  const items = await response.json() as FlashSaleItem[]
  if (!Array.isArray(items)) throw new Error('活动列表响应无法读取，请稍后重新加载。')
  return items
}

function isOrder(value: unknown): value is Order {
  if (!value || typeof value !== 'object') return false
  const order = value as Partial<Order>
  const money = (amount: unknown) => typeof amount === 'string' && /^\d+\.\d{2}$/.test(amount)
  return (
    typeof order.public_id === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(order.public_id) &&
    typeof order.status === 'string' &&
    ['PENDING_PAYMENT', 'PAID', 'SHIPPED', 'COMPLETED', 'CANCELED', 'EXPIRED'].includes(order.status) &&
    order.currency === 'CNY' && money(order.total) &&
    Array.isArray(order.items) && order.items.length === 1 &&
    order.items[0]?.quantity === 1 && money(order.items[0]?.unit_price)
  )
}

export async function createFlashSaleOrder(
  intent: FlashSaleIntent,
): Promise<{ order: Order; created: boolean }> {
  const token = await ensureCsrfCookie()
  const response = await fetch('/api/flash-sales/orders/', {
    method: 'POST', credentials: 'same-origin',
    headers: {
      Accept: 'application/json', 'Content-Type': 'application/json',
      'X-CSRFToken': token, 'Idempotency-Key': intent.key,
    },
    body: JSON.stringify({ flash_sale_item_id: intent.itemId, address_id: intent.addressId }),
  })
  if (!response.ok) return rejectResponse(response)
  const order: unknown = await response.json()
  // 解析失败或异常成功响应都不能被当成确认：原 Key 和参数仍须保留。
  if (![200, 201].includes(response.status) || !isOrder(order)) {
    throw new Error('订单响应无法确认，请用原购买信息重试。')
  }
  return { order, created: response.status === 201 }
}
