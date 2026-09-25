import { ensureCsrfCookie } from './auth'
import type { Address, AddressInput } from '../types/address'

const ADDRESS_API_BASE = '/api/addresses'

export class AddressApiError extends Error {
  readonly status: number
  readonly code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = 'AddressApiError'
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

async function addressRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  // Cookie 保持登录身份；地址归属和默认地址规则仍由后端校验，前端不能靠隐藏按钮保证权限。
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body) headers.set('Content-Type', 'application/json')

  const response = await fetch(`${ADDRESS_API_BASE}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers,
  })
  if (!response.ok) {
    const details = await errorDetails(response)
    throw new AddressApiError(response.status, details.message, details.code)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function csrfHeaders(): Promise<Record<string, string>> {
  return { 'X-CSRFToken': await ensureCsrfCookie() }
}

export function fetchAddresses(signal?: AbortSignal): Promise<Address[]> {
  return addressRequest<Address[]>('/', { method: 'GET', signal })
}

export async function createAddress(input: AddressInput): Promise<Address> {
  return addressRequest<Address>('/', {
    method: 'POST',
    headers: await csrfHeaders(),
    body: JSON.stringify(input),
  })
}

export async function updateAddress(
  addressId: number,
  input: Partial<AddressInput>,
): Promise<Address> {
  return addressRequest<Address>(`/${addressId}/`, {
    method: 'PATCH',
    headers: await csrfHeaders(),
    body: JSON.stringify(input),
  })
}

export async function deleteAddress(addressId: number): Promise<void> {
  return addressRequest<void>(`/${addressId}/`, {
    method: 'DELETE',
    headers: await csrfHeaders(),
  })
}

export async function setDefaultAddress(
  addressId: number,
): Promise<Address> {
  return addressRequest<Address>(`/${addressId}/default/`, {
    method: 'POST',
    headers: await csrfHeaders(),
  })
}
