<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { confirmReceipt, fetchOrder, OrderApiError } from '../api/orders'
import {
  createPaymentAttempt,
  fetchPaymentAttempts,
  PaymentApiError,
  reconcilePaymentAttempt,
} from '../api/payments'
import type { Order } from '../types/order'
import type { PaymentAttempt } from '../types/payment'

const route = useRoute()
const publicId = computed(() => String(route.params.publicId ?? ''))
const order = ref<Order | null>(null)
const attempts = ref<PaymentAttempt[]>([])
const loading = ref(true)
const busyAction = ref('')
const loginRequired = ref(false)
const errorMessage = ref('')
const notice = ref('')
let controller: AbortController | undefined
let inMemoryPaymentKey: string | null = null

const latestAttempt = computed(() => attempts.value[0] ?? null)
// 活跃 Attempt 的网关结果可能仍在途中，此时只能查原记录，不能开启第二次支付。
const activeAttempt = computed(() => {
  const attempt = latestAttempt.value
  return attempt && ['CREATED', 'PENDING', 'UNKNOWN'].includes(attempt.status)
    ? attempt
    : null
})

const orderStatusLabels: Record<string, string> = {
  PENDING_PAYMENT: '待支付',
  PAID: '已支付，待发货',
  SHIPPED: '已发货',
  COMPLETED: '已完成',
  CANCELED: '已取消',
  EXPIRED: '已过期',
}

const attemptStatusLabels: Record<string, string> = {
  CREATED: '已创建',
  PENDING: '处理中',
  UNKNOWN: '结果待确认',
  SUCCEEDED: '成功',
  FAILED: '失败',
  EXPIRED: '已过期',
  CANCELED: '已取消',
}

function paymentKeyStorageName(): string {
  return `project-one-payment-key:${publicId.value}`
}

// 每单单独保存 Key；会话存储可用时，页面刷新或网络断开后仍能复用同一支付意图。
function readPaymentKey(): string | null {
  try {
    return sessionStorage.getItem(paymentKeyStorageName()) ?? inMemoryPaymentKey
  } catch {
    return inMemoryPaymentKey
  }
}

function savePaymentKey(key: string): void {
  inMemoryPaymentKey = key
  try {
    sessionStorage.setItem(paymentKeyStorageName(), key)
  } catch {
    // 浏览器禁用会话存储时，本页面仍可继续完成请求。
  }
}

function clearPaymentKey(): void {
  inMemoryPaymentKey = null
  try {
    sessionStorage.removeItem(paymentKeyStorageName())
  } catch {
    // 浏览器禁用会话存储时无须清理。
  }
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function showError(error: unknown): void {
  // 403 由后端决定身份边界；页面只据此展示登录入口，不承担权限校验。
  loginRequired.value =
    ((error instanceof OrderApiError || error instanceof PaymentApiError) &&
      error.status === 403)
  errorMessage.value = loginRequired.value
    ? '请先登录，再查看和处理自己的订单。'
    : error instanceof Error && error.message
      ? error.message
      : '操作失败，请稍后重试。'
}

function paymentConfirmed(): boolean {
  return order.value?.status === 'PAID'
}

async function loadDetail(showLoading = true): Promise<void> {
  // 同时取订单和支付尝试，但支付事实仍以服务端状态为准；路由变化时取消旧请求。
  controller?.abort()
  const currentController = new AbortController()
  controller = currentController
  if (showLoading) loading.value = true
  errorMessage.value = ''
  loginRequired.value = false
  try {
    const [orderResult, attemptResults] = await Promise.all([
      fetchOrder(publicId.value, currentController.signal),
      fetchPaymentAttempts(publicId.value, currentController.signal),
    ])
    if (currentController.signal.aborted) return
    order.value = orderResult
    attempts.value = attemptResults
    // 已进入订单下一状态或本次尝试已终结，旧 Key 就不能代表新的支付意图。
    if (
      orderResult.status !== 'PENDING_PAYMENT' ||
      ['FAILED', 'EXPIRED', 'CANCELED', 'SUCCEEDED'].includes(attemptResults[0]?.status ?? '')
    ) {
      clearPaymentKey()
    }
  } catch (error) {
    if (!currentController.signal.aborted) showError(error)
  } finally {
    if (!currentController.signal.aborted) loading.value = false
  }
}

async function startPayment(): Promise<void> {
  if (order.value?.status !== 'PENDING_PAYMENT' || activeAttempt.value) return
  const orderId = publicId.value
  busyAction.value = 'pay'
  errorMessage.value = ''
  notice.value = ''
  const key = readPaymentKey() ?? `payment-${crypto.randomUUID()}`
  savePaymentKey(key)
  try {
    // 先把 Key 持久在本次浏览器会话中，再发请求；响应丢失时仍可安全重试同一意图。
    const returnedAttempt = await createPaymentAttempt(orderId, key)
    if (orderId !== publicId.value) return
    if (!['CREATED', 'PENDING', 'UNKNOWN'].includes(returnedAttempt.status)) {
      clearPaymentKey()
    }
    await loadDetail(false)
    if (!errorMessage.value) {
      notice.value = paymentConfirmed()
        ? '模拟支付已由服务端确认。'
        : ['FAILED', 'EXPIRED', 'CANCELED'].includes(returnedAttempt.status)
          ? '这次模拟支付已结束，可重新发起一次。'
          : '请求已提交，请以服务端订单状态为准。'
    }
  } catch (error) {
    if (orderId !== publicId.value) return
    // HTTP 报错也可能发生在后端提交之后，先重新读取订单，避免把已成功支付误报为失败。
    await loadDetail(false)
    if (paymentConfirmed()) {
      errorMessage.value = ''
      notice.value = '服务端已确认模拟支付。'
    } else {
      showError(error)
    }
  } finally {
    busyAction.value = ''
  }
}

async function queryPayment(): Promise<void> {
  const attempt = activeAttempt.value
  if (!attempt) return
  const orderId = publicId.value
  busyAction.value = 'reconcile'
  errorMessage.value = ''
  notice.value = ''
  try {
    // UNKNOWN/PENDING 只查当前 Attempt；重复点按钮不会创建另一笔支付。
    const reconciledAttempt = await reconcilePaymentAttempt(attempt.public_id)
    if (orderId !== publicId.value) return
    if (!['CREATED', 'PENDING', 'UNKNOWN'].includes(reconciledAttempt.status)) {
      clearPaymentKey()
    }
    await loadDetail(false)
    if (!errorMessage.value) {
      notice.value = paymentConfirmed()
        ? '服务端已确认模拟支付，订单现在是已支付状态。'
        : '已查询支付结果，请查看下方最新状态。'
    }
  } catch (error) {
    if (orderId !== publicId.value) return
    await loadDetail(false)
    if (paymentConfirmed()) {
      errorMessage.value = ''
      notice.value = '服务端已确认模拟支付。'
    } else {
      showError(error)
    }
  } finally {
    busyAction.value = ''
  }
}

async function completeOrder(): Promise<void> {
  if (order.value?.status !== 'SHIPPED') return
  const orderId = publicId.value
  busyAction.value = 'confirm'
  errorMessage.value = ''
  notice.value = ''
  try {
    const updatedOrder = await confirmReceipt(orderId)
    if (orderId !== publicId.value) return
    order.value = updatedOrder
    notice.value = '收货已确认，订单完成。'
  } catch (error) {
    if (orderId !== publicId.value) return
    // 确认收货的响应可能丢失；重新读取状态才能区分真实失败与已完成。
    await loadDetail(false)
    if (order.value?.status === 'COMPLETED') {
      errorMessage.value = ''
      notice.value = '收货已确认，订单完成。'
    } else {
      showError(error)
    }
  } finally {
    busyAction.value = ''
  }
}

watch(
  publicId,
  () => {
    // 复用同一个组件浏览另一张订单时，先清除旧订单的展示与内存 Key。
    order.value = null
    attempts.value = []
    inMemoryPaymentKey = null
    notice.value = ''
    void loadDetail()
  },
  { immediate: true },
)
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section aria-labelledby="order-heading">
    <RouterLink class="back-link" :to="{ name: 'orders' }">← 返回我的订单</RouterLink>

    <div v-if="loading" class="state-panel" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <h2>正在加载订单</h2>
    </div>

    <div v-else-if="loginRequired" class="state-panel error-panel" role="alert">
      <h2>需要登录</h2>
      <p>{{ errorMessage }}</p>
      <RouterLink
        class="primary-button"
        :to="{ name: 'login', query: { redirect: `/orders/${publicId}` } }"
      >
        前往登录
      </RouterLink>
    </div>

    <div v-else-if="!order" class="state-panel error-panel" role="alert">
      <h2>订单暂时无法显示</h2>
      <p>{{ errorMessage || '该订单不存在，或你没有查看权限。' }}</p>
      <button class="primary-button" type="button" @click="loadDetail()">重新加载</button>
    </div>

    <template v-else>
      <div class="page-heading">
        <div>
          <p class="eyebrow">Order Detail</p>
          <h1 id="order-heading">订单详情</h1>
          <p class="heading-copy">订单号 {{ order.public_id }}</p>
        </div>
        <button
          class="secondary-button"
          type="button"
          :disabled="Boolean(busyAction)"
          @click="loadDetail(false)"
        >
          刷新状态
        </button>
      </div>

      <p v-if="errorMessage" class="inline-error order-action-feedback" role="alert">
        {{ errorMessage }}
      </p>
      <p v-if="notice" class="success-message order-action-feedback" role="status">
        {{ notice }}
      </p>

      <div class="order-detail-layout">
        <div class="order-detail-main">
          <article class="order-summary-card">
            <p class="sku-code">{{ formatDate(order.created_at) }}</p>
            <h2>订单状态：{{ orderStatusLabels[order.status] ?? order.status }}</h2>
            <p v-if="order.status === 'PENDING_PAYMENT'" class="muted">
              商品库存已预留，等待模拟支付。
            </p>
            <p v-else-if="order.status === 'PAID'" class="muted">
              模拟支付已确认，等待管理员模拟发货。
            </p>
            <p v-else-if="order.status === 'SHIPPED'" class="muted">
              管理员已模拟发货，请确认收货。
            </p>
            <p v-else-if="order.status === 'COMPLETED'" class="muted">
              收货已确认，订单完成。
            </p>
          </article>

          <section class="checkout-card">
            <h2>商品快照</h2>
            <article v-for="(item, index) in order.items" :key="index" class="checkout-item">
              <div>
                <strong>{{ item.product_name_snapshot }}</strong>
                <small>{{ item.sku_code_snapshot }} · {{ item.color_snapshot || '默认规格' }}</small>
              </div>
              <span>{{ item.quantity }} × ¥{{ item.unit_price }}</span>
              <strong>¥{{ item.line_total }}</strong>
            </article>
          </section>

          <section class="checkout-card">
            <h2>收货地址快照</h2>
            <p class="muted order-address">
              {{ order.address.recipient_name }} · {{ order.address.phone }}<br />
              {{ order.address.province }}{{ order.address.city }}{{ order.address.district }}{{ order.address.detail }}
            </p>
          </section>

          <section v-if="attempts.length > 0" class="checkout-card">
            <h2>模拟支付尝试</h2>
            <p class="muted">支付状态以服务端记录为准，本项目不处理真实资金。</p>
            <div v-for="attempt in attempts" :key="attempt.public_id" class="payment-attempt-row">
              <span>{{ formatDate(attempt.created_at) }}</span>
              <strong>{{ attemptStatusLabels[attempt.status] ?? attempt.status }}</strong>
            </div>
          </section>
        </div>

        <aside class="cart-summary order-detail-summary">
          <h2>金额与下一步</h2>
          <p><span>商品小计</span><strong>¥{{ order.subtotal }}</strong></p>
          <p><span>优惠</span><strong>- ¥{{ order.discount_total }}</strong></p>
          <p><span>运费</span><strong>¥{{ order.shipping_fee }}</strong></p>
          <p class="cart-total"><span>应付金额</span><strong>¥{{ order.total }}</strong></p>

          <template v-if="order.status === 'PENDING_PAYMENT'">
            <button
              v-if="activeAttempt"
              class="primary-button order-action-button"
              type="button"
              :disabled="Boolean(busyAction)"
              @click="queryPayment"
            >
              {{ busyAction === 'reconcile' ? '正在查询…' : '查询支付结果' }}
            </button>
            <button
              v-else
              class="primary-button order-action-button"
              type="button"
              :disabled="Boolean(busyAction)"
              @click="startPayment"
            >
              {{ busyAction === 'pay' ? '正在提交…' : '发起模拟支付' }}
            </button>
            <small v-if="activeAttempt">
              这次支付尚未确认，请查询原支付尝试，不要重复发起。
            </small>
          </template>

          <button
            v-if="order.status === 'SHIPPED'"
            class="primary-button order-action-button"
            type="button"
            :disabled="Boolean(busyAction)"
            @click="completeOrder"
          >
            {{ busyAction === 'confirm' ? '正在确认…' : '确认收货' }}
          </button>

          <small v-if="order.status === 'PAID'">
            管理员模拟发货后，刷新状态即可确认收货。
          </small>
          <small v-if="order.status === 'COMPLETED'">该订单已完成。</small>
          <small v-if="order.status === 'CANCELED' || order.status === 'EXPIRED'">
            该订单已结束，不能继续支付。
          </small>
        </aside>
      </div>
    </template>
  </section>
</template>
