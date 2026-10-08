<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchOrders, OrderApiError } from '../api/orders'
import type { PaginatedResponse } from '../types/catalog'
import type { Order } from '../types/order'

const PAGE_SIZE = 20
const route = useRoute()
const router = useRouter()
const orderPage = ref<PaginatedResponse<Order> | null>(null)
const orders = computed(() => orderPage.value?.results ?? [])
const currentPage = computed(() => {
  const rawPage = route.query.page
  if (rawPage === undefined) return 1
  if (typeof rawPage !== 'string' || !/^[1-9]\d*$/.test(rawPage)) return null
  const page = Number(rawPage)
  return Number.isSafeInteger(page) ? page : null
})
const totalPages = computed(() => {
  return Math.max(1, Math.ceil((orderPage.value?.count ?? 0) / PAGE_SIZE))
})
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
  // 页码留在 URL；取消旧请求并检查取消状态，避免慢响应覆盖新页面。
  controller?.abort()
  const currentController = new AbortController()
  controller = currentController
  loading.value = true
  loginRequired.value = false
  errorMessage.value = ''
  orderPage.value = null
  if (currentPage.value === null) {
    errorMessage.value = '页码无效，请返回第一页。'
    loading.value = false
    return
  }
  try {
    const result = await fetchOrders(currentPage.value, currentController.signal)
    if (!currentController.signal.aborted) orderPage.value = result
  } catch (error) {
    if (currentController.signal.aborted) return
    loginRequired.value = error instanceof OrderApiError && error.status === 403
    errorMessage.value = loginRequired.value
      ? '请先登录，再查看自己的订单。'
      : error instanceof OrderApiError && error.status === 404
        ? '这一页不存在，请返回第一页。'
        : error instanceof Error && error.message
          ? error.message
          : '订单暂时无法加载，请稍后重试。'
  } finally {
    if (!currentController.signal.aborted) loading.value = false
  }
}

function goToPage(page: number): void {
  void router.push({
    name: 'orders',
    query: { ...route.query, page: String(page) },
  })
}

watch(() => route.query.page, () => void loadOrders(), { immediate: true })
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
      <RouterLink class="primary-button" :to="{ name: 'login', query: { redirect: route.fullPath } }">
        前往登录
      </RouterLink>
    </div>

    <div v-else-if="errorMessage" class="state-panel order-list-state error-panel" role="alert">
      <h2>订单暂时无法加载</h2>
      <p>{{ errorMessage }}</p>
      <button class="primary-button" type="button" @click="loadOrders">重新加载</button>
      <button v-if="currentPage !== 1" class="secondary-button" type="button" @click="goToPage(1)">
        返回第一页
      </button>
    </div>

    <div v-else-if="orders.length === 0" class="state-panel order-list-state">
      <h2>还没有订单</h2>
      <p>先选择商品并完成下单。</p>
      <RouterLink class="primary-button" :to="{ name: 'catalog-products' }">
        浏览商品
      </RouterLink>
    </div>

    <template v-else-if="orderPage">
      <div class="order-list">
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
      <nav v-if="orderPage.count > PAGE_SIZE" class="pagination" aria-label="订单分页">
        <button
          class="secondary-button"
          type="button"
          :disabled="orderPage.previous === null"
          @click="goToPage((currentPage ?? 1) - 1)"
        >
          上一页
        </button>
        <span>第 {{ currentPage }} / {{ totalPages }} 页 · 共 {{ orderPage.count }} 笔</span>
        <button
          class="secondary-button"
          type="button"
          :disabled="orderPage.next === null"
          @click="goToPage((currentPage ?? 1) + 1)"
        >
          下一页
        </button>
      </nav>
    </template>
  </section>
</template>
