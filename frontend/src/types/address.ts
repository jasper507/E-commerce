export interface Address {
  id: number
  recipient_name: string
  phone: string
  province: string
  city: string
  district: string
  detail: string
  is_default: boolean
}

export interface AddressInput {
  recipient_name: string
  phone: string
  province: string
  city: string
  district: string
  detail: string
  is_default?: boolean
}
