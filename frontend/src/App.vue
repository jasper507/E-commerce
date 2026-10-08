<template>
  <div class="app-shell">
    <header class="site-header">
      <RouterLink class="brand" :to="{ name: 'catalog-products' }">
        <span class="brand-mark" aria-hidden="true">P1</span>
        <span>
          <strong>Project One</strong>
          <small>Interview Core 商店</small>
        </span>
      </RouterLink>

      <nav aria-label="主导航">
        <RouterLink :to="{ name: 'catalog-products' }">浏览商品</RouterLink>
        <RouterLink :to="{ name: 'flash-sales' }">限时秒杀</RouterLink>
        <RouterLink :to="{ name: 'cart' }">购物车</RouterLink>
        <RouterLink :to="{ name: 'checkout' }">结账</RouterLink>
        <RouterLink :to="{ name: 'orders' }">我的订单</RouterLink>
        <RouterLink :to="{ name: 'addresses' }">收货地址</RouterLink>
        <RouterLink :to="{ name: 'login' }">登录</RouterLink>
      </nav>
    </header>

    <main class="page-shell">
      <RouterView />
    </main>

    <footer class="site-footer">
      <p>目录中的价格与库存状态仅供浏览，下单时以后端重新校验为准。</p>
    </footer>
  </div>
</template>
