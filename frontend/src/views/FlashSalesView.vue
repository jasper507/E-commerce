<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchAddresses } from '../api/addresses'
import { AuthApiError, fetchCurrentUser } from '../api/auth'
import { createFlashSaleOrder, fetchFlashSaleItems, FlashSaleApiError } from '../api/flashSales'
import { clearFlashSaleIntent, readFlashSaleIntent, saveFlashSaleIntent } from '../flashSaleIntent'
import type { Address } from '../types/address'
import type { SessionUser } from '../types/auth'
import type { FlashSaleIntent, FlashSaleItem } from '../types/flashSale'
import type { Order } from '../types/order'

type PurchaseState = 'choosing' | 'submitting' | 'pending' | 'rejected' | 'confirmed'

const items = ref<FlashSaleItem[]>([])
const addresses = ref<Address[]>([])
const user = ref<SessionUser | null>(null)
const intent = ref<FlashSaleIntent | null>(null)
const order = ref<Order | null>(null)
const state = ref<PurchaseState>('choosing')
const selectedItemId = ref<number | null>(null)
const selectedAddressId = ref<number | null>(null)
const loading = ref(true)
const listError = ref('')
const sessionError = ref('')
const storageError = ref('')
const feedback = ref('')
const loginRequired = ref(false)

const selectedItem = computed(() => items.value.find((item) => item.id === selectedItemId.value))
const canBuy = computed(() =>
  !loading.value && user.value !== null && intent.value === null && !storageError.value &&
  selectedItem.value?.in_stock === true &&
  addresses.value.some((address) => address.id === selectedAddressId.value),
)
const canStartNew = computed(() => ['confirmed', 'rejected'].includes(state.value))

function message(error: unknown): string {
  return error instanceof Error ? error.message : '请求暂时无法完成，请稍后重试。'
}

async function loadItems(): Promise<void> {
  listError.value = ''
  try {
    items.value = await fetchFlashSaleItems()
  } catch (error) {
    listError.value = message(error)
  }
}

async function loadSession(): Promise<void> {
  try {
    user.value = await fetchCurrentUser()
    if (!Number.isSafeInteger(user.value.id) || user.value.id <= 0) throw new Error('无法确认当前登录用户。')
    try {
      intent.value = readFlashSaleIntent(user.value.id)
      if (intent.value) state.value = 'pending'
    } catch (error) {
      storageError.value = message(error)
    }
    // 地址加载失败不阻止用已保存的地址 ID 确认原订单。
    addresses.value = await fetchAddresses()
    selectedAddressId.value = addresses.value.find((address) => address.is_default)?.id ?? addresses.value[0]?.id ?? null
  } catch (error) {
    loginRequired.value = error instanceof AuthApiError && error.status === 403
    sessionError.value = loginRequired.value ? '请登录后选择收货地址。' : message(error)
  }
}

async function submitIntent(): Promise<void> {
  const original = intent.value
  if (!original || state.value === 'submitting') return
  state.value = 'submitting'
  feedback.value = ''
  try {
    // 每次发送前核对当前身份，不能把另一个账号保存的购买意图提交给新账号。
    const currentUser = await fetchCurrentUser()
    if (currentUser.id !== original.userId) {
      throw new Error('登录账号已变化，请刷新页面后处理当前账号的购买。原购买信息已保留。')
    }
    const result = await createFlashSaleOrder(original)
    order.value = result.order
    state.value = 'confirmed'
    feedback.value = result.created ? '订单创建成功。' : '已确认原订单，没有重复下单。'
  } catch (error) {
    state.value = 'pending'
    const rejectedCodes = [
      'address_not_found', 'flash_sale_item_not_found', 'flash_sale_unavailable',
      'category_inactive', 'product_inactive', 'sku_inactive',
      'flash_sale_limit_reached', 'insufficient_stock',
    ]
    if (error instanceof FlashSaleApiError && [404, 409].includes(error.status) && rejectedCodes.includes(error.code ?? '')) {
      state.value = 'rejected'
      feedback.value = error.message
    } else if (error instanceof FlashSaleApiError && error.code === 'idempotency_conflict') {
      feedback.value = '购买信息与原请求冲突，请先核对“我的订单”。原购买信息已保留。'
    } else if ((error instanceof FlashSaleApiError || error instanceof AuthApiError) && error.status === 403) {
      feedback.value = '登录状态或安全校验未通过。请核对登录状态后确认原购买；购买信息已保留。'
    } else if (error instanceof FlashSaleApiError && ['stock_busy', 'checkout_busy'].includes(error.code ?? '')) {
      feedback.value = '下单繁忙，请稍后确认原购买。购买信息已保留。'
    } else {
      feedback.value = `结果尚未确认：${message(error)} 原购买信息已保留。`
    }
  }
}

async function buy(): Promise<void> {
  if (!canBuy.value || !user.value || selectedItemId.value === null || selectedAddressId.value === null) return
  const original = Object.freeze({
    userId: user.value.id, key: `flash-sale-${crypto.randomUUID()}`,
    itemId: selectedItemId.value, addressId: selectedAddressId.value,
  })
  try {
    saveFlashSaleIntent(original)
  } catch {
    storageError.value = '浏览器无法保存购买信息，因此尚未发送下单请求。请启用会话存储后刷新。'
    return
  }
  intent.value = original
  await submitIntent()
}

function startNew(): void {
  if (!canStartNew.value || !intent.value) return
  try {
    clearFlashSaleIntent(intent.value.userId)
  } catch {
    feedback.value = '无法清理上一购买信息，请刷新后先确认原订单。'
    return
  }
  intent.value = null
  order.value = null
  state.value = 'choosing'
  feedback.value = ''
  selectedItemId.value = null
  void loadItems()
}

onMounted(async () => {
  await Promise.all([loadItems(), loadSession()])
  loading.value = false
})
</script>

<template>
  <section aria-labelledby="flash-sale-heading">
    <div class="page-heading">
      <div>
        <h1 id="flash-sale-heading">限时秒杀</h1>
        <p class="heading-copy">每个活动商品限购一件，成交价格和库存以下单结果为准。</p>
      </div>
    </div>

    <p v-if="loading" role="status">正在加载活动和购买信息…</p>
    <div v-if="sessionError || storageError" class="catalog-boundary flash-sale-notice" role="alert">
      <p>{{ storageError || sessionError }}</p>
      <RouterLink v-if="loginRequired" :to="{ name: 'login', query: { redirect: '/flash-sales' } }">前往登录</RouterLink>
      <RouterLink v-else :to="{ name: 'orders' }">我的订单</RouterLink>
    </div>

    <article v-if="intent" class="checkout-card flash-sale-intent" aria-labelledby="purchase-heading" aria-live="polite">
      <h2 id="purchase-heading">{{ state === 'confirmed' ? '订单已确认' : state === 'rejected' ? '本次购买未成功' : state === 'submitting' ? '正在提交原购买' : '购买结果待确认' }}</h2>
      <p>{{ feedback || '请确认上次购买结果，再开始新的购买。' }}</p>
      <p class="muted">已保存活动项 {{ intent.itemId }}、地址 {{ intent.addressId }}。确认时继续使用原购买信息。</p>
      <div v-if="order">
        <p>订单号：{{ order.public_id }}</p>
        <p>状态：{{ order.status }} · 应付 ¥{{ order.total }}</p>
        <RouterLink class="primary-button" :to="{ name: 'order-detail', params: { publicId: order.public_id } }">查看订单与模拟支付</RouterLink>
      </div>
      <div class="flash-sale-actions">
        <button v-if="state === 'pending'" class="primary-button" type="button" @click="submitIntent">确认原购买</button>
        <button v-if="canStartNew" class="secondary-button" type="button" @click="startNew">开始新购买</button>
        <RouterLink :to="{ name: 'orders' }">我的订单</RouterLink>
        <RouterLink :to="{ name: 'login', query: { redirect: '/flash-sales' } }">核对登录</RouterLink>
      </div>
    </article>

    <p v-if="listError" class="inline-error" role="alert">{{ listError }}</p>
    <button v-if="listError" class="secondary-button" type="button" @click="loadItems">重新加载活动</button>
    <p v-if="!loading && !listError && items.length === 0">当前没有开放的秒杀活动。</p>
    <div class="product-grid flash-sale-items">
      <article v-for="item in items" :key="item.id" class="product-card">
        <div>
          <p class="muted">{{ item.sale_name }}</p>
          <h2>{{ item.product_name }}</h2>
          <p>{{ item.sku_code }} · {{ item.color }}</p>
          <p class="price">¥{{ item.price }} <small>／一件</small></p>
          <p :class="['stock-badge', { unavailable: !item.in_stock }]">{{ item.in_stock ? '当前有货' : '当前无货' }}</p>
        </div>
        <label class="flash-sale-choice">
          <input v-model="selectedItemId" type="radio" name="flash-sale-item" :value="item.id" :disabled="!user || !!intent || !item.in_stock || loading || !!storageError" />
          选择 {{ item.product_name }}（{{ item.sku_code }}）
        </label>
      </article>
    </div>

    <form v-if="user && !intent" class="checkout-card flash-sale-checkout" @submit.prevent="buy">
      <h2>选择收货地址</h2>
      <label v-for="address in addresses" :key="address.id" class="checkout-address">
        <input v-model="selectedAddressId" type="radio" name="flash-sale-address" :value="address.id" :disabled="loading || !!storageError" />
        <span>{{ address.recipient_name }} · {{ address.province }}{{ address.city }}{{ address.district }}{{ address.detail }}</span>
      </label>
      <p v-if="!loading && addresses.length === 0">暂无收货地址，请先添加本人的地址。</p>
      <RouterLink :to="{ name: 'addresses' }">管理收货地址</RouterLink>
      <button class="primary-button checkout-submit" type="submit" :disabled="!canBuy">购买一件</button>
    </form>
  </section>
</template>

<style scoped>
.flash-sale-notice { margin: 1rem 0; }
.flash-sale-intent { margin: 1.5rem 0; overflow-wrap: anywhere; }
.flash-sale-items, .flash-sale-checkout { margin-top: 1.5rem; }
.flash-sale-choice { display: flex; gap: 0.5rem; align-items: center; }
.flash-sale-actions { display: flex; flex-wrap: wrap; gap: 1rem; align-items: center; }
.price small { font-size: 0.9rem; }
.stock-badge { margin-top: 1rem; }
</style>
