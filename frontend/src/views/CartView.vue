<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import {
  CartApiError,
  deleteCartItem,
  fetchCart,
  updateCartItem,
} from '../api/cart'
import type { Cart } from '../types/cart'

const cart = ref<Cart | null>(null)
const quantities = reactive<Record<string, number>>({})
const loading = ref(true)
const busySku = ref('')
const errorMessage = ref('')
const loginRequired = ref(false)
let controller: AbortController | undefined

const reasonLabels: Record<string, string> = {
  category_inactive: '商品分类已下架',
  product_inactive: '商品已下架',
  sku_inactive: '该规格已下架',
  insufficient_stock: '当前库存不足',
}

function syncQuantities(value: Cart): void {
  for (const item of value.items) quantities[item.sku_code] = item.quantity
}

function showError(error: unknown): void {
  loginRequired.value = error instanceof CartApiError && error.status === 403
  errorMessage.value = loginRequired.value
    ? '请先登录，再查看购物车。'
    : error instanceof Error && error.message
      ? error.message
      : '购物车操作失败，请稍后重试。'
}

async function loadCart(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  errorMessage.value = ''
  loginRequired.value = false
  try {
    const result = await fetchCart(controller.signal)
    cart.value = result
    syncQuantities(result)
  } catch (error) {
    if (!controller.signal.aborted) showError(error)
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}

async function saveQuantity(skuCode: string): Promise<void> {
  busySku.value = skuCode
  errorMessage.value = ''
  try {
    const result = await updateCartItem(skuCode, quantities[skuCode])
    cart.value = result
    syncQuantities(result)
  } catch (error) {
    showError(error)
    const original = cart.value?.items.find((item) => item.sku_code === skuCode)
    if (original) quantities[skuCode] = original.quantity
  } finally {
    busySku.value = ''
  }
}

async function removeItem(skuCode: string): Promise<void> {
  busySku.value = skuCode
  errorMessage.value = ''
  try {
    await deleteCartItem(skuCode)
    const result = await fetchCart()
    cart.value = result
    syncQuantities(result)
  } catch (error) {
    showError(error)
  } finally {
    busySku.value = ''
  }
}

onMounted(() => void loadCart())
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section aria-labelledby="cart-heading">
    <div class="page-heading">
      <div>
        <p class="eyebrow">Authenticated Cart</p>
        <h1 id="cart-heading">我的购物车</h1>
        <p class="heading-copy">购物车不锁价、不预占库存；下单时会重新校验。</p>
      </div>
    </div>

    <div v-if="loading" class="state-panel" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <h2>正在加载购物车</h2>
    </div>

    <div v-else-if="loginRequired" class="state-panel error-panel" role="alert">
      <h2>需要登录</h2>
      <p>{{ errorMessage }}</p>
      <RouterLink class="primary-button" :to="{ name: 'login', query: { redirect: '/cart' } }">
        前往登录
      </RouterLink>
    </div>

    <div v-else-if="!cart" class="state-panel error-panel" role="alert">
      <h2>购物车暂时无法加载</h2>
      <p>{{ errorMessage }}</p>
      <button class="primary-button" type="button" @click="loadCart">重新加载</button>
    </div>

    <div v-else-if="cart.items.length === 0" class="state-panel">
      <h2>购物车还是空的</h2>
      <p>去商品详情页选择一个有货 SKU。</p>
      <RouterLink class="primary-button" :to="{ name: 'catalog-products' }">
        浏览商品
      </RouterLink>
    </div>

    <template v-else>
      <p v-if="errorMessage" class="inline-error cart-error" role="alert">
        {{ errorMessage }}
      </p>
      <div class="cart-layout">
        <div class="cart-items">
          <article v-for="item in cart.items" :key="item.sku_code" class="cart-item">
            <div>
              <p class="sku-code">{{ item.sku_code }}</p>
              <h2>{{ item.product_name }}</h2>
              <p class="muted">{{ item.color || '默认规格' }} · 单价 ¥{{ item.unit_price }}</p>
              <p v-if="!item.is_available" class="inline-error">
                {{ reasonLabels[item.unavailable_reason ?? ''] ?? '当前不可购买' }}
              </p>
            </div>
            <div class="cart-item-actions">
              <label>
                数量
                <input
                  v-model.number="quantities[item.sku_code]"
                  type="number"
                  min="1"
                  max="99"
                  :disabled="busySku === item.sku_code || !item.is_available"
                />
              </label>
              <button
                class="secondary-button"
                type="button"
                :disabled="busySku === item.sku_code || !item.is_available"
                @click="saveQuantity(item.sku_code)"
              >
                更新
              </button>
              <button
                class="text-button danger-text"
                type="button"
                :disabled="busySku === item.sku_code"
                @click="removeItem(item.sku_code)"
              >
                删除
              </button>
              <strong>¥{{ item.line_total }}</strong>
            </div>
          </article>
        </div>

        <aside class="cart-summary">
          <h2>当前汇总</h2>
          <p><span>商品种类</span><strong>{{ cart.item_count }}</strong></p>
          <p><span>商品数量</span><strong>{{ cart.total_quantity }}</strong></p>
          <p class="cart-total"><span>当前小计</span><strong>¥{{ cart.subtotal }}</strong></p>
          <small>这不是订单价格，也不代表库存已被保留。</small>
        </aside>
      </div>
    </template>
  </section>
</template>
