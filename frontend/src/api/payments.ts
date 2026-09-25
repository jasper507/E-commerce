import { ensureCsrfCookie } from './auth'
import type { PaymentAttempt } from '../types/payment'

const ATTEMPT_API_BASE = '/api/payments/attempts'

export class PaymentApiError extends Error {
  readonly status: number
  readonly code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = 'PaymentApiError'
    this.status = status
    this.code = code
  }
}

async function paymentRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  // 统一保留后端错误码与 HTTP 状态，页面才能区分未登录、业务冲突和普通请求失败。
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body) headers.set('Content-Type', 'application/json')

  const response = await fetch(`${ATTEMPT_API_BASE}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers,
  })
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`
    let code: string | undefined
    try {
      const payload = (await response.json()) as Record<string, unknown>
      if (typeof payload.detail === 'string') message = payload.detail
      if (typeof payload.code === 'string') code = payload.code
      if (message.startsWith('请求失败')) {
        const firstFieldError = Object.values(payload).find(
          (value) => Array.isArray(value) && typeof value[0] === 'string',
        )
        if (Array.isArray(firstFieldError)) message = firstFieldError[0]
      }
    } catch {
      // 非 JSON 错误使用稳定提示。
    }
    throw new PaymentApiError(response.status, message, code)
  }
  return (await response.json()) as T
}

export function fetchPaymentAttempts(
  orderPublicId: string,
  signal?: AbortSignal,
): Promise<PaymentAttempt[]> {
  const query = new URLSearchParams({ order_public_id: orderPublicId })
  return paymentRequest<PaymentAttempt[]>(`/?${query}`, { signal })
}

export async function createPaymentAttempt(
  orderPublicId: string,
  idempotencyKey: string,
): Promise<PaymentAttempt> {
  // Key 标识“这一次”支付意图；超时后复用它，不能立即换 Key 制造第二次支付。
  return paymentRequest<PaymentAttempt>('/', {
    method: 'POST',
    headers: {
      'X-CSRFToken': await ensureCsrfCookie(),
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ order_public_id: orderPublicId }),
  })
}

export async function reconcilePaymentAttempt(
  attemptPublicId: string,
): Promise<PaymentAttempt> {
  // UNKNOWN 表示结果尚不确定；查原 Attempt，由后端向 Mock Gateway 核实，不在浏览器判定成功。
  return paymentRequest<PaymentAttempt>(
    `/${encodeURIComponent(attemptPublicId)}/reconcile/`,
    {
      method: 'POST',
      headers: { 'X-CSRFToken': await ensureCsrfCookie() },
    },
  )
}
