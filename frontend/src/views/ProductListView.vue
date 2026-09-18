<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchCategories, fetchProducts } from '../api/catalog'
import type {
  Category,
  PaginatedResponse,
  ProductSummary,
} from '../types/catalog'

const PAGE_SIZE = 20

const route = useRoute()
const router = useRouter()

const categories = ref<Category[]>([])
const categoryLoading = ref(true)
const categoryError = ref('')

const productPage = ref<PaginatedResponse<ProductSummary> | null>(null)
const productLoading = ref(true)
const productError = ref('')

let categoryController: AbortController | undefined
let productController: AbortController | undefined

const selectedCategory = computed(() => {
  return typeof route.query.category === 'string' ? route.query.category : ''
})

const currentPage = computed(() => {
  const rawPage = typeof route.query.page === 'string' ? route.query.page : '1'
  const parsedPage = Number(rawPage)
  return Number.isInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1
})

const totalPages = computed(() => {
  if (!productPage.value) {
    return 1
  }
  return Math.max(1, Math.ceil(productPage.value.count / PAGE_SIZE))
})

const emptyMessage = computed(() => {
  return selectedCategory.value
    ? '该分类暂时没有已上架商品。'
    : '目前还没有可公开浏览的商品。'
})

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function readableError(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return '暂时无法加载商品，请稍后重试。'
}

async function loadCategories(): Promise<void> {
  categoryController?.abort()
  const controller = new AbortController()
  categoryController = controller
  categoryLoading.value = true
  categoryError.value = ''

  try {
    const result = await fetchCategories(controller.signal)
    if (!controller.signal.aborted) categories.value = result
  } catch (error) {
    if (!controller.signal.aborted && !isAbortError(error)) {
      categoryError.value = readableError(error)
    }
  } finally {
    if (!controller.signal.aborted) {
      categoryLoading.value = false
    }
  }
}

async function loadProducts(): Promise<void> {
  productController?.abort()
  const controller = new AbortController()
  productController = controller
  productLoading.value = true
  productError.value = ''

  try {
    const result = await fetchProducts(
      {
        category: selectedCategory.value || undefined,
        page: currentPage.value,
      },
      controller.signal,
    )
    if (!controller.signal.aborted) productPage.value = result
  } catch (error) {
    if (!controller.signal.aborted && !isAbortError(error)) {
      productPage.value = null
      productError.value = readableError(error)
    }
  } finally {
    if (!controller.signal.aborted) {
      productLoading.value = false
    }
  }
}

function selectCategory(category: string): void {
  void router.push({
    name: 'catalog-products',
    query: category ? { category } : {},
  })
}

function goToPage(page: number): void {
  const query: Record<string, string> = {}
  if (selectedCategory.value) {
    query.category = selectedCategory.value
  }
  if (page > 1) {
    query.page = String(page)
  }

  void router.push({ name: 'catalog-products', query })
}

onMounted(() => {
  void loadCategories()
})

watch([selectedCategory, currentPage], () => void loadProducts(), {
  immediate: true,
})

onBeforeUnmount(() => {
  categoryController?.abort()
  productController?.abort()
})
</script>

<template>
  <section aria-labelledby="catalog-heading">
    <div class="page-heading">
      <div>
        <p class="eyebrow">Guest Catalog Browse</p>
        <h1 id="catalog-heading">浏览商品</h1>
        <p class="heading-copy">查看已上架商品、价格范围与当前库存提示。</p>
      </div>
      <p v-if="productPage && !productLoading" class="result-count">
        共 {{ productPage.count }} 件商品
      </p>
    </div>

    <section class="filter-panel" aria-labelledby="category-heading">
      <div class="filter-heading">
        <h2 id="category-heading">商品分类</h2>
        <button
          v-if="categoryError"
          class="text-button"
          type="button"
          @click="loadCategories"
        >
          重试加载分类
        </button>
      </div>

      <p v-if="categoryLoading" class="muted" role="status">正在加载分类…</p>
      <p v-else-if="categoryError" class="inline-error" role="alert">
        分类加载失败：{{ categoryError }}
      </p>
      <div v-else class="filter-list" aria-label="按分类筛选">
        <button
          type="button"
          :class="['filter-chip', { active: selectedCategory === '' }]"
          :aria-pressed="selectedCategory === ''"
          @click="selectCategory('')"
        >
          全部商品
        </button>
        <button
          v-for="category in categories"
          :key="category.slug"
          type="button"
          :class="['filter-chip', { active: selectedCategory === category.slug }]"
          :aria-pressed="selectedCategory === category.slug"
          @click="selectCategory(category.slug)"
        >
          {{ category.name }}
        </button>
      </div>
    </section>

    <div v-if="productLoading" class="state-panel" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <h2>正在加载商品</h2>
      <p>正在向 Django Catalog API 获取当前页数据。</p>
    </div>

    <div v-else-if="productError" class="state-panel error-panel" role="alert">
      <p class="state-code">请求失败</p>
      <h2>商品暂时无法加载</h2>
      <p>{{ productError }}</p>
      <button class="primary-button" type="button" @click="loadProducts">
        重新加载
      </button>
      <button
        v-if="currentPage > 1"
        class="secondary-button"
        type="button"
        @click="goToPage(1)"
      >
        返回第一页
      </button>
    </div>

    <div
      v-else-if="productPage && productPage.results.length === 0"
      class="state-panel"
    >
      <p class="state-code">Empty</p>
      <h2>没有找到商品</h2>
      <p>{{ emptyMessage }}</p>
      <button
        v-if="selectedCategory"
        class="secondary-button"
        type="button"
        @click="selectCategory('')"
      >
        查看全部商品
      </button>
    </div>

    <template v-else-if="productPage">
      <div class="product-grid">
        <article
          v-for="product in productPage.results"
          :key="product.slug"
          class="product-card"
        >
          <div class="product-card-topline">
            <span class="category-label">{{ product.category.name }}</span>
            <span :class="['stock-badge', { unavailable: !product.in_stock }]">
              {{ product.in_stock ? '有货' : '暂时缺货' }}
            </span>
          </div>

          <div>
            <h2>{{ product.name }}</h2>
            <p class="price">
              {{
                product.lowest_price === null
                  ? '暂无可售规格'
                  : `¥${product.lowest_price} 起`
              }}
            </p>
          </div>

          <RouterLink
            class="card-link"
            :to="{
              name: 'catalog-product-detail',
              params: { slug: product.slug },
              query: route.query,
            }"
          >
            查看商品详情
            <span aria-hidden="true">→</span>
          </RouterLink>
        </article>
      </div>

      <nav
        v-if="productPage.count > PAGE_SIZE"
        class="pagination"
        aria-label="商品分页"
      >
        <button
          class="secondary-button"
          type="button"
          :disabled="productPage.previous === null"
          @click="goToPage(currentPage - 1)"
        >
          上一页
        </button>
        <span>第 {{ currentPage }} / {{ totalPages }} 页</span>
        <button
          class="secondary-button"
          type="button"
          :disabled="productPage.next === null"
          @click="goToPage(currentPage + 1)"
        >
          下一页
        </button>
      </nav>
    </template>
  </section>
</template>
