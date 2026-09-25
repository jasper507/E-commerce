<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { fetchOrders, OrderApiError } from '../api/orders'
import type { Order } from '../types/order'

const orders = ref<Order[]>([])
const loading = ref(true)
const loginRequired = ref(false)
const errorMessage = ref('')
let controller: AbortController | undefined

const statusLabels: Record<string, string> = {
  PENDING_PAYMENT: '待支付',
  PAID: '已支付，待发货',
  SHIPPED: '已发货',
  COMPLETED: '已完成',
  CANCELED: '已取消',
  EXPIRED: '已过期',
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

async function loadOrders(): Promise<void> {
  // “刷新订单”只请求当前用户的数据；取消上次请求，避免旧响应覆盖最新状态。
  controller?.abort()
  const currentController = new AbortController()
  controller = currentController
  loading.value = true
  loginRequired.value = false
  errorMessage.value = ''
  try {
    orders.value = await fetchOrders(currentController.signal)
  } catch (error) {
    if (currentController.signal.aborted) return
    loginRequired.value = error instanceof OrderApiError && error.status === 403
    errorMessage.value = loginRequired.value
      ? '请先登录，再查看自己的订单。'
      : error instanceof Error && error.message
        ? error.message
        : '订单暂时无法加载，请稍后重试。'
  } finally {
    if (!currentController.signal.aborted) loading.value = false
  }
}

onMounted(() => void loadOrders())
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section aria-labelledby="orders-heading">
    <div class="page-heading">
      <div>
        <p class="eyebrow">My Orders</p>
        <h1 id="orders-heading">我的订单</h1>
        <p class="heading-copy">查看下单、模拟支付和收货的进度。</p>
      </div>
      <button class="secondary-button" type="button" :disabled="loading" @click="loadOrders">
        刷新订单
      </button>
    </div>

    <div v-if="loading" class="state-panel order-list-state" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <h2>正在加载订单</h2>
    </div>

    <div v-else-if="loginRequired" class="state-panel order-list-state error-panel" role="alert">
      <h2>需要登录</h2>
      <p>{{ errorMessage }}</p>
      <RouterLink class="primary-button" :to="{ name: 'login', query: { redirect: '/orders' } }">
        前往登录
      </RouterLink>
    </div>

    <div v-else-if="errorMessage" class="state-panel order-list-state error-panel" role="alert">
      <h2>订单暂时无法加载</h2>
      <p>{{ errorMessage }}</p>
      <button class="primary-button" type="button" @click="loadOrders">重新加载</button>
    </div>

    <div v-else-if="orders.length === 0" class="state-panel order-list-state">
      <h2>还没有订单</h2>
      <p>先选择商品并完成下单。</p>
      <RouterLink class="primary-button" :to="{ name: 'catalog-products' }">
        浏览商品
      </RouterLink>
    </div>

    <div v-else class="order-list">
      <article v-for="order in orders" :key="order.public_id" class="order-list-card">
        <div class="order-list-heading">
          <div>
            <p class="sku-code">{{ formatDate(order.created_at) }}</p>
            <h2>订单 {{ order.public_id }}</h2>
          </div>
          <span class="stock-badge">{{ statusLabels[order.status] ?? order.status }}</span>
        </div>
        <p class="muted">{{ order.items.length }} 种商品 · 收货人 {{ order.address.recipient_name }}</p>
        <div class="order-list-footer">
          <strong>应付 ¥{{ order.total }}</strong>
          <RouterLink
            class="primary-button"
            :to="{ name: 'order-detail', params: { publicId: order.public_id } }"
          >
            查看详情
          </RouterLink>
        </div>
      </article>
    </div>
  </section>
</template>
