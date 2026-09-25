<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { login } from '../api/auth'

const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const submitting = ref(false)
const errorMessage = ref('')

function safeRedirect(): string {
  // 只接受站内相对路径，避免把登录后的用户重定向到外部恶意站点。
  const candidate = route.query.redirect
  return typeof candidate === 'string' &&
    candidate.startsWith('/') &&
    !candidate.startsWith('//')
    ? candidate
    : '/products'
}

async function submit(): Promise<void> {
  submitting.value = true
  errorMessage.value = ''
  try {
    await login(username.value, password.value)
    await router.replace(safeRedirect())
  } catch (error) {
    errorMessage.value =
      error instanceof Error && error.message
        ? error.message
        : '登录失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="auth-card" aria-labelledby="login-heading">
    <p class="eyebrow">Session Authentication</p>
    <h1 id="login-heading">登录后使用购物车</h1>
    <p class="muted">使用你在 Django 中创建的本地账号。密码只发送给 Django，不保存在浏览器代码中。</p>

    <form class="stack-form" @submit.prevent="submit">
      <label>
        用户名
        <input v-model.trim="username" autocomplete="username" required />
      </label>
      <label>
        密码
        <input
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
        />
      </label>
      <p v-if="errorMessage" class="inline-error" role="alert">
        {{ errorMessage }}
      </p>
      <button class="primary-button" type="submit" :disabled="submitting">
        {{ submitting ? '正在登录…' : '登录' }}
      </button>
    </form>
  </section>
</template>
