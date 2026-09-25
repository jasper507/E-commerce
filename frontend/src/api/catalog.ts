import type {
  Category,
  PaginatedResponse,
  ProductDetail,
  ProductSummary,
} from '../types/catalog'

const CATALOG_API_BASE = '/api/catalog'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown }
    if (typeof payload.detail === 'string') {
      return payload.detail
    }
  } catch {
    // 非 JSON 错误响应仍会使用下面的稳定提示。
  }

  return `请求失败（HTTP ${response.status}）`
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  // AbortSignal 让页面切换筛选条件时取消旧请求，避免旧结果覆盖新结果。
  const response = await fetch(`${CATALOG_API_BASE}${path}`, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
    },
    signal,
  })

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response))
  }

  return (await response.json()) as T
}

export function fetchCategories(signal?: AbortSignal): Promise<Category[]> {
  return getJson<Category[]>('/categories/', signal)
}

export interface ProductListParams {
  category?: string
  page?: number
}

export function fetchProducts(
  params: ProductListParams,
  signal?: AbortSignal,
): Promise<PaginatedResponse<ProductSummary>> {
  const searchParams = new URLSearchParams()

  if (params.category) {
    searchParams.set('category', params.category)
  }
  if (params.page && params.page > 1) {
    searchParams.set('page', String(params.page))
  }

  const query = searchParams.toString()
  return getJson<PaginatedResponse<ProductSummary>>(
    `/products/${query ? `?${query}` : ''}`,
    signal,
  )
}

export function fetchProductDetail(
  slug: string,
  signal?: AbortSignal,
): Promise<ProductDetail> {
  return getJson<ProductDetail>(`/products/${encodeURIComponent(slug)}/`, signal)
}
