export interface Category {
  slug: string
  name: string
}

export interface ProductSummary {
  slug: string
  name: string
  category: Category
  lowest_price: string | null
  in_stock: boolean
}

export interface ProductSku {
  code: string
  color: string
  attributes: Record<string, unknown>
  price: string
  // 这是浏览时的库存提示，不是下单预占成功的承诺。
  in_stock: boolean
}

export interface ProductDetail {
  slug: string
  name: string
  description: string
  category: Category
  in_stock: boolean
  skus: ProductSku[]
}

export interface PaginatedResponse<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}
