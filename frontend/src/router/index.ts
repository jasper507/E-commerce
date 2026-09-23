import { createRouter, createWebHistory } from 'vue-router'

import NotFoundView from '../views/NotFoundView.vue'
import AddressesView from '../views/AddressesView.vue'
import CartView from '../views/CartView.vue'
import CheckoutView from '../views/CheckoutView.vue'
import LoginView from '../views/LoginView.vue'
import ProductDetailView from '../views/ProductDetailView.vue'
import ProductListView from '../views/ProductListView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: { name: 'catalog-products' },
    },
    {
      path: '/products',
      name: 'catalog-products',
      component: ProductListView,
    },
    {
      path: '/products/:slug',
      name: 'catalog-product-detail',
      component: ProductDetailView,
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
    },
    {
      path: '/cart',
      name: 'cart',
      component: CartView,
    },
    {
      path: '/addresses',
      name: 'addresses',
      component: AddressesView,
    },
    {
      path: '/checkout',
      name: 'checkout',
      component: CheckoutView,
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: NotFoundView,
    },
  ],
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition ?? { top: 0 }
  },
})

export default router
