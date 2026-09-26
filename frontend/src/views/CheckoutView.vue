<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { AddressApiError, fetchAddresses } from '../api/addresses'
import { CartApiError, fetchCart } from '../api/cart'
import { createOrder, OrderApiError } from '../api/orders'
import type { Address } from '../types/address'
import type { Cart } from '../types/cart'
import type { Order } from '../types/order'

const addresses = ref<Address[]>([])
const cart = ref<Cart | null>(null)
const selectedAddressId = ref<number | null>(null)
const order = ref<Order | null>(null)
const loading = ref(true)
const submitting = ref(false)
const loginRequired = ref(false)
const errorMessage = ref('')
const idempotencyKey = ref(newIdempotencyKey())

const canSubmit = computed(
  () =>
    selectedAddressId.value !== null &&
    (cart.value?.items.length ?? 0) > 0 &&
    cart.value?.items.every((item) => item.is_available) === true,
)

// 每次明确的新下单意图使用新 Key；同一次请求结果不确定时继续复用现有 Key。
function newIdempotencyKey(): string {
  return `checkout-${crypto.randomUUID()}`
}

function showError(error: unknown): void {
  loginRequired.value =
    ((error instanceof OrderApiError ||
      error instanceof CartApiError ||
      error instanceof AddressApiError) &&
      error.status === 403)
  if (error instanceof OrderApiError && error.code === 'stock_busy') {
    errorMessage.value = '商品正在被其他顾客下单，请稍后重试。'
    return
  }
  if (error instanceof OrderApiError && error.code === 'insufficient_stock') {
    errorMessage.value = '商品库存不足，无法下单。'
    return
  }
  errorMessage.value = loginRequired.value
    ? '请先登录，再提交订单。'
    : error instanceof Error && error.message
      ? error.message
      : '结账信息加载失败，请稍后重试。'
}

async function loadCheckout(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  loginRequired.value = false
  try {
    const [cartResult, addressResult] = await Promise.all([
      fetchCart(),
      fetchAddresses(),
    ])
    cart.value = cartResult
    addresses.value = addressResult
    selectedAddressId.value =
      addressResult.find((address) => address.is_default)?.id ??
      addressResult[0]?.id ??
      null
  } catch (error) {
    showError(error)
  } finally {
    loading.value = false
  }
}

async function submitOrder(): Promise<void> {
  if (selectedAddressId.value === null || !canSubmit.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    // 页面金额只是提示；服务端会重新读 SKU 价格并原子预占库存。
    order.value = await createOrder(
      selectedAddressId.value,
      idempotencyKey.value,
    )
  } catch (error) {
    showError(error)
  } finally {
    submitting.value = false
  }
}

watch(selectedAddressId, () => {
  // 地址变更意味着请求内容变了，不能沿用旧 Key，否则后端会按“同 Key 异参数”返回 409。
  idempotencyKey.value = newIdempotencyKey()
})

onMounted(() => void loadCheckout())
</script>

<template>
  <section aria-labelledby="checkout-heading">
    <div class="page-heading">
      <div>
        <p class="eyebrow">MVP-3 Transaction Boundary</p>
        <h1 id="checkout-heading">确认并创建订单</h1>
        <p class="heading-copy">
          后端会重新计算金额、检查库存，并在一个 MySQL 事务中完成快照与库存预留。
        </p>
      </div>
    </div>

    <div v-if="loading" class="state-panel" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <h2>正在加载结账信息</h2>
    </div>

    <div v-else-if="loginRequired" class="state-panel error-panel" role="alert">
      <h2>需要登录</h2>
      <p>{{ errorMessage }}</p>
      <RouterLink class="primary-button" :to="{ name: 'login', query: { redirect: '/checkout' } }">
        前往登录
      </RouterLink>
    </div>

    <div v-else-if="order" class="order-success">
      <div class="success-message">
        订单创建成功，库存已从可售库存移动到预留库存。
      </div>
      <article class="order-summary-card">
        <p class="sku-code">公开订单号</p>
        <h2>{{ order.public_id }}</h2>
        <p><span>状态</span><strong>{{ order.status }}</strong></p>
        <p><span>商品小计</span><strong>¥{{ order.subtotal }}</strong></p>
        <p><span>优惠</span><strong>- ¥{{ order.discount_total }}</strong></p>
        <p><span>运费</span><strong>¥{{ order.shipping_fee }}</strong></p>
        <p class="cart-total"><span>应付金额</span><strong>¥{{ order.total }}</strong></p>
        <p class="muted">
          收货：{{ order.address.recipient_name }}，{{ order.address.province }}{{ order.address.city }}{{ order.address.district }}{{ order.address.detail }}
        </p>
      </article>
      <RouterLink
        class="primary-button"
        :to="{ name: 'order-detail', params: { publicId: order.public_id } }"
      >
        查看订单
      </RouterLink>
      <RouterLink class="secondary-button" :to="{ name: 'catalog-products' }">
        继续浏览商品
      </RouterLink>
    </div>

    <template v-else-if="cart">
      <p v-if="errorMessage" class="inline-error checkout-error" role="alert">
        {{ errorMessage }}
      </p>

      <div v-if="cart.items.length === 0" class="state-panel">
        <h2>购物车为空</h2>
        <p>请先选择一个有库存的 SKU。</p>
        <RouterLink class="primary-button" :to="{ name: 'catalog-products' }">
          浏览商品
        </RouterLink>
      </div>

      <div v-else class="checkout-layout">
        <div class="checkout-main">
          <section class="checkout-card">
            <div class="section-heading">
              <div>
                <p class="eyebrow">Address Snapshot Source</p>
                <h2>选择收货地址</h2>
              </div>
              <RouterLink class="text-button" :to="{ name: 'addresses' }">
                管理地址
              </RouterLink>
            </div>

            <div v-if="addresses.length === 0" class="state-panel compact">
              <h3>还没有收货地址</h3>
              <RouterLink class="primary-button" :to="{ name: 'addresses' }">
                新增地址
              </RouterLink>
            </div>
            <template v-else>
              <label
                v-for="address in addresses"
                :key="address.id"
                class="checkout-address"
              >
                <input v-model="selectedAddressId" type="radio" :value="address.id" />
                <span>
                  <strong>{{ address.recipient_name }} · {{ address.phone }}</strong>
                  <small>{{ address.province }}{{ address.city }}{{ address.district }}{{ address.detail }}</small>
                </span>
                <span v-if="address.is_default" class="stock-badge">默认</span>
              </label>
            </template>
          </section>

          <section class="checkout-card">
            <p class="eyebrow">Server Recalculation</p>
            <h2>商品快照来源</h2>
            <article v-for="item in cart.items" :key="item.sku_code" class="checkout-item">
              <div>
                <strong>{{ item.product_name }}</strong>
                <small>{{ item.sku_code }} · {{ item.color || '默认规格' }}</small>
              </div>
              <span>{{ item.quantity }} × ¥{{ item.unit_price }}</span>
              <strong>¥{{ item.line_total }}</strong>
            </article>
          </section>
        </div>

        <aside class="cart-summary">
          <h2>下单确认</h2>
          <p><span>当前小计</span><strong>¥{{ cart.subtotal }}</strong></p>
          <p><span>优惠</span><strong>¥0.00</strong></p>
          <p><span>运费</span><strong>¥0.00</strong></p>
          <p class="cart-total"><span>预计应付</span><strong>¥{{ cart.subtotal }}</strong></p>
          <small>页面金额只供确认，订单最终金额由后端重新读取 SKU 价格计算。</small>
          <button
            class="primary-button checkout-submit"
            type="button"
            :disabled="submitting || !canSubmit"
            @click="submitOrder"
          >
            {{ submitting ? '正在创建订单…' : '创建待支付订单' }}
          </button>
        </aside>
      </div>
    </template>

    <div v-else class="state-panel error-panel" role="alert">
      <h2>结账信息无法加载</h2>
      <p>{{ errorMessage }}</p>
      <button class="primary-button" type="button" @click="loadCheckout">重新加载</button>
    </div>
  </section>
</template>
