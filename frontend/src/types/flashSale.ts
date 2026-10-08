export interface FlashSaleItem {
  id: number
  sale_name: string
  starts_at: string
  ends_at: string
  product_slug: string
  product_name: string
  sku_code: string
  color: string
  attributes: Record<string, unknown>
  price: string
  currency: 'CNY'
  quantity: 1
  in_stock: boolean
}

export interface FlashSaleIntent {
  readonly userId: number
  readonly key: string
  readonly itemId: number
  readonly addressId: number
}
