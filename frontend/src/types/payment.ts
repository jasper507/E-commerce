export type PaymentAttemptStatus =
  | 'CREATED'
  | 'PENDING'
  | 'UNKNOWN'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'EXPIRED'
  | 'CANCELED'

// UNKNOWN 是结果尚未确认，不等于 FAILED；可否再发起由后端状态机决定。
export interface PaymentAttempt {
  public_id: string
  order_public_id: string
  status: PaymentAttemptStatus
  amount: string
  currency: 'CNY'
  created_at: string
}
