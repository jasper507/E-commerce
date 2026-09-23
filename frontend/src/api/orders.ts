import { ensureCsrfCookie } from './auth'
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
