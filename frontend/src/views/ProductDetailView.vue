<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { addCartItem, CartApiError } from '../api/cart'
import { ApiError, fetchProductDetail } from '../api/catalog'
import type { ProductDetail } from '../types/catalog'

const route = useRoute()
const router = useRouter()

const product = ref<ProductDetail | null>(null)
const loading = ref(true)
const errorMessage = ref('')
const notFound = ref(false)
const addingSkuCode = ref('')
const cartMessage = ref('')
const cartError = ref('')

let requestController: AbortController | undefined

const slug = computed(() => {
  return typeof route.params.slug === 'string' ? route.params.slug : ''
})

const listRoute = computed(() => ({
  name: 'catalog-products',
  query: route.query,
}))

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function formatAttributeValue(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number') {
    return String(value)
  }
  if (typeof value === 'boolean') {
    return value ? '是' : '否'
  }
  if (value === null) {
    return '—'
  }
  return JSON.stringify(value)
}

async function addToCart(skuCode: string): Promise<void> {
  addingSkuCode.value = skuCode
  cartMessage.value = ''
  cartError.value = ''
  try {
    const cart = await addCartItem(skuCode, 1)
    cartMessage.value = `已加入购物车，当前共 ${cart.total_quantity} 件商品。`
  } catch (error) {
    if (error instanceof CartApiError && error.status === 403) {
      // 未登录时保留当前商品路由，登录成功后回到原来的购买位置。
      await router.push({
        name: 'login',
        query: { redirect: route.fullPath },
      })
      return
    }
    cartError.value =
      error instanceof Error && error.message
        ? error.message
        : '加入购物车失败，请稍后重试。'
  } finally {
    addingSkuCode.value = ''
  }
}

async function loadProduct(): Promise<void> {
  // 路由中的商品变化后取消旧请求，防止上一件商品的结果短暂显示在新地址下。
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  loading.value = true
  errorMessage.value = ''
  notFound.value = false
  product.value = null

  try {
    const result = await fetchProductDetail(slug.value, controller.signal)
    if (!controller.signal.aborted) product.value = result
  } catch (error) {
    if (controller.signal.aborted || isAbortError(error)) {
      return
    }
    if (error instanceof ApiError && error.status === 404) {
      notFound.value = true
    } else {
      errorMessage.value =
        error instanceof Error && error.message
          ? error.message
          : '暂时无法加载商品详情，请稍后重试。'
    }
  } finally {
    if (!controller.signal.aborted) {
      loading.value = false
    }
  }
}

watch(slug, () => void loadProduct(), { immediate: true })

onBeforeUnmount(() => requestController?.abort())
</script>

<template>
  <RouterLink class="back-link" :to="listRoute">← 返回商品列表</RouterLink>

  <div v-if="loading" class="state-panel" role="status">
    <span class="spinner" aria-hidden="true"></span>
    <h1>正在加载商品详情</h1>
    <p>正在获取商品及其已上架 SKU。</p>
  </div>

  <div v-else-if="notFound" class="state-panel error-panel" role="alert">
    <p class="state-code">404</p>
    <h1>商品不存在或已下架</h1>
    <p>公开接口不会区分“不存在”“商品下架”或“所属分类下架”。</p>
    <RouterLink class="primary-button" :to="listRoute">返回商品列表</RouterLink>
  </div>

  <div v-else-if="errorMessage" class="state-panel error-panel" role="alert">
    <p class="state-code">请求失败</p>
    <h1>商品详情暂时无法加载</h1>
    <p>{{ errorMessage }}</p>
    <button class="primary-button" type="button" @click="loadProduct">重新加载</button>
  </div>

  <article v-else-if="product" class="product-detail">
    <header class="detail-header">
      <div>
        <p class="eyebrow">{{ product.category.name }}</p>
        <h1>{{ product.name }}</h1>
        <p class="detail-description">
          {{ product.description || '该商品暂时没有详细介绍。' }}
        </p>
      </div>
      <span :class="['stock-badge', 'large', { unavailable: !product.in_stock }]">
        {{ product.in_stock ? '当前有货' : '当前缺货' }}
      </span>
    </header>

    <section class="sku-section" aria-labelledby="sku-heading">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Purchasable Variants</p>
          <h2 id="sku-heading">可选规格</h2>
        </div>
        <p class="muted">共 {{ product.skus.length }} 个已上架 SKU</p>
      </div>

      <p v-if="cartMessage" class="success-message" role="status">
        {{ cartMessage }}
      </p>
      <p v-if="cartError" class="inline-error" role="alert">
        {{ cartError }}
      </p>

      <div v-if="product.skus.length === 0" class="state-panel compact">
        <h3>暂无可售规格</h3>
        <p>商品可以被浏览，但目前没有已上架 SKU，因此不能购买。</p>
      </div>

      <div v-else class="sku-list">
        <article v-for="sku in product.skus" :key="sku.code" class="sku-card">
          <div class="sku-main">
            <div>
              <p class="sku-code">{{ sku.code }}</p>
              <h3>{{ sku.color || '默认规格' }}</h3>
            </div>
            <div class="sku-price-block">
              <strong>¥{{ sku.price }}</strong>
              <span :class="['stock-badge', { unavailable: !sku.in_stock }]">
                {{ sku.in_stock ? '有货' : '缺货' }}
              </span>
            </div>
          </div>

          <dl v-if="Object.keys(sku.attributes).length" class="attribute-list">
            <div v-for="(value, key) in sku.attributes" :key="key">
              <dt>{{ key }}</dt>
              <dd>{{ formatAttributeValue(value) }}</dd>
            </div>
          </dl>
          <button
            class="primary-button add-cart-button"
            type="button"
            :disabled="!sku.in_stock || addingSkuCode === sku.code"
            @click="addToCart(sku.code)"
          >
            {{
              !sku.in_stock
                ? '暂时缺货'
                : addingSkuCode === sku.code
                  ? '正在加入…'
                  : '加入购物车'
            }}
          </button>
        </article>
      </div>
    </section>

    <aside class="catalog-boundary" aria-label="库存提示说明">
      <strong>为什么这里只显示“有货 / 缺货”？</strong>
      <p>
        目录页不会公开库存数量。这里的状态只是浏览时的快照；未来加入购物车和下单后，Django
        必须再次读取 MySQL 中的权威价格、上下架状态和库存。
      </p>
    </aside>
  </article>
</template>
