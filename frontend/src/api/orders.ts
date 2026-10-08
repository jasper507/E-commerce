import { ensureCsrfCookie } from './auth'
import type { PaginatedResponse } from '../types/catalog'
import type { Order } from '../types/order'

const ORDER_API_BASE = '/api/orders'

export class OrderApiError extends Error {
  readonly status: number
  readonly code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = 'OrderApiError'
    this.status = status
    this.code = code
  }
}

async function errorDetails(
  response: Response,
): Promise<{ message: string; code?: string }> {
  try {
    const payload = (await response.json()) as Record<string, unknown>
    if (typeof payload.detail === 'string') {
      return {
        message: payload.detail,
        code: typeof payload.code === 'string' ? payload.code : undefined,
      }
    }
    for (const value of Object.values(payload)) {
      if (Array.isArray(value) && typeof value[0] === 'string') {
        return { message: value[0] }
      }
    }
  } catch {
    // 非 JSON 错误使用下面的稳定提示。
  }
  return { message: `请求失败（HTTP ${response.status}）` }
}

export async function createOrder(
  addressId: number,
  idempotencyKey: string,
): Promise<Order> {
  // 同一次下单重试要复用 Idempotency-Key：网络断开不代表后端没有创建订单。
  // 地址 ID 只是用户选择；价格、库存、快照和订单归属全部由 Django 在事务中确认。
  const response = await fetch(`${ORDER_API_BASE}/`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-CSRFToken': await ensureCsrfCookie(),
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ address_id: addressId }),
  })
  if (!response.ok) {
    const details = await errorDetails(response)
    throw new OrderApiError(response.status, details.message, details.code)
  }
  return (await response.json()) as Order
}

export async function fetchOrders(
  page = 1,
  signal?: AbortSignal,
): Promise<PaginatedResponse<Order>> {
  const searchParams = new URLSearchParams({ page: String(page) })
  const response = await fetch(`${ORDER_API_BASE}/?${searchParams}`, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    signal,
  })
  if (!response.ok) {
    const details = await errorDetails(response)
    throw new OrderApiError(response.status, details.message, details.code)
  }
  return (await response.json()) as PaginatedResponse<Order>
}

export async function fetchOrder(
  publicId: string,
  signal?: AbortSignal,
): Promise<Order> {
  const response = await fetch(
    `${ORDER_API_BASE}/${encodeURIComponent(publicId)}/`,
    {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal,
    },
  )
  if (!response.ok) {
    const details = await errorDetails(response)
    throw new OrderApiError(response.status, details.message, details.code)
  }
  return (await response.json()) as Order
}

export async function confirmReceipt(publicId: string): Promise<Order> {
  // 页面只发出“确认收货”意图；是否属于本人、是否已发货，由后端状态机判断。
  const response = await fetch(
    `${ORDER_API_BASE}/${encodeURIComponent(publicId)}/confirm-receipt/`,
    {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'X-CSRFToken': await ensureCsrfCookie(),
      },
    },
  )
  if (!response.ok) {
    const details = await errorDetails(response)
    throw new OrderApiError(response.status, details.message, details.code)
  }
  return (await response.json()) as Order
}
