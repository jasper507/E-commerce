import type { SessionUser } from '../types/auth'

const AUTH_API_BASE = '/api/auth'

export class AuthApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'AuthApiError'
    this.status = status
  }
}

function csrfToken(): string {
  const prefix = 'csrftoken='
  const cookie = document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : ''
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as {
      detail?: unknown
      non_field_errors?: unknown
    }
    if (typeof payload.detail === 'string') return payload.detail
    if (
      Array.isArray(payload.non_field_errors) &&
      typeof payload.non_field_errors[0] === 'string'
    ) {
      return payload.non_field_errors[0]
    }
  } catch {
    // 非 JSON 错误使用下面的稳定提示。
  }
  return `请求失败（HTTP ${response.status}）`
}

export async function ensureCsrfCookie(): Promise<string> {
  const response = await fetch(`${AUTH_API_BASE}/csrf/`, {
    credentials: 'same-origin',
  })
  if (!response.ok) {
    throw new AuthApiError(response.status, await errorMessage(response))
  }

  const token = csrfToken()
  if (!token) throw new Error('浏览器没有收到 CSRF Token。')
  return token
}

export async function login(
  username: string,
  password: string,
): Promise<SessionUser> {
  const token = await ensureCsrfCookie()
  const response = await fetch(`${AUTH_API_BASE}/login/`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-CSRFToken': token,
    },
    body: JSON.stringify({ username, password }),
  })
  if (!response.ok) {
    throw new AuthApiError(response.status, await errorMessage(response))
  }
  return (await response.json()) as SessionUser
}

export async function fetchCurrentUser(): Promise<SessionUser> {
  const response = await fetch(`${AUTH_API_BASE}/me/`, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new AuthApiError(response.status, await errorMessage(response))
  }
  return (await response.json()) as SessionUser
}
