export interface OrderAddressSnapshot {
  recipient_name: string
  phone: string
  province: string
  city: string
  district: string
  detail: string
}

export interface OrderItemSnapshot {
  product_name_snapshot: string
  sku_code_snapshot: string
  color_snapshot: string
  attributes_snapshot: Record<string, unknown>
  unit_price: string
  quantity: number
  discount_amount: string
  line_total: string
}

export interface Order {
  public_id: string
  status: string
  currency: 'CNY'
  subtotal: string
  discount_total: string
  shipping_fee: string
  total: string
  address: OrderAddressSnapshot
  items: OrderItemSnapshot[]
  created_at: string
}
