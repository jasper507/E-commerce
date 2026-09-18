export interface CartItem {
  sku_code: string
  product_slug: string
  product_name: string
  color: string
  attributes: Record<string, unknown>
  unit_price: string
  quantity: number
  line_total: string
  is_available: boolean
  unavailable_reason: string | null
}

export interface Cart {
  items: CartItem[]
  item_count: number
  total_quantity: number
  subtotal: string
}
