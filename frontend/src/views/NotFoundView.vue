<template>
  <section class="state-panel error-panel" role="alert">
    <p class="state-code">404</p>
    <h1>页面不存在</h1>
    <p>你访问的前端地址没有对应页面。</p>
    <RouterLink class="primary-button" :to="{ name: 'catalog-products' }">
      返回商品列表
    </RouterLink>
  </section>
</template>
