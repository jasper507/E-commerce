import { ensureCsrfCookie } from './auth'
import type { Cart } from '../types/cart'

const CART_API_BASE = '/api/cart'

export class CartApiError extends Error {
  readonly status: number
  readonly code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = 'CartApiError'
    this.status = status
    this.code = code
  }
}

async function errorDetails(
  response: Response,
): Promise<{ message: string; code?: string }> {
  try {
    const payload = (await response.json()) as {
      detail?: unknown
      code?: unknown
      quantity?: unknown
    }
    if (typeof payload.detail === 'string') {
      return {
        message: payload.detail,
        code: typeof payload.code === 'string' ? payload.code : undefined,
      }
    }
    if (Array.isArray(payload.quantity) && typeof payload.quantity[0] === 'string') {
      return { message: payload.quantity[0] }
    }
  } catch {
    // 非 JSON 错误使用下面的稳定提示。
  }
  return { message: `请求失败（HTTP ${response.status}）` }
}

async function cartRequest(
  path: string,
  init: RequestInit = {},
): Promise<Cart> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body) headers.set('Content-Type', 'application/json')

  const response = await fetch(`${CART_API_BASE}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers,
  })
  if (!response.ok) {
    const details = await errorDetails(response)
    throw new CartApiError(response.status, details.message, details.code)
  }
  return (await response.json()) as Cart
}

async function csrfHeaders(): Promise<Record<string, string>> {
  return { 'X-CSRFToken': await ensureCsrfCookie() }
}

export function fetchCart(signal?: AbortSignal): Promise<Cart> {
  return cartRequest('/', { method: 'GET', signal })
}

export async function addCartItem(
  skuCode: string,
  quantity: number,
): Promise<Cart> {
  return cartRequest('/items/', {
    method: 'POST',
    headers: await csrfHeaders(),
    body: JSON.stringify({ sku_code: skuCode, quantity }),
  })
}

export async function updateCartItem(
  skuCode: string,
  quantity: number,
): Promise<Cart> {
  return cartRequest(`/items/${encodeURIComponent(skuCode)}/`, {
    method: 'PATCH',
    headers: await csrfHeaders(),
    body: JSON.stringify({ quantity }),
  })
}

export async function deleteCartItem(skuCode: string): Promise<void> {
  const response = await fetch(
    `${CART_API_BASE}/items/${encodeURIComponent(skuCode)}/`,
    {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(await csrfHeaders()),
      },
    },
  )
  if (!response.ok) {
    const details = await errorDetails(response)
    throw new CartApiError(response.status, details.message, details.code)
  }
}
