<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import {
  AddressApiError,
  createAddress,
  deleteAddress,
  fetchAddresses,
  setDefaultAddress,
  updateAddress,
} from '../api/addresses'
import type { Address, AddressInput } from '../types/address'

interface AddressForm extends AddressInput {
  id: number | null
  is_default: boolean
}

const emptyForm = (): AddressForm => ({
  id: null,
  recipient_name: '',
  phone: '',
  province: '',
  city: '',
  district: '',
  detail: '',
  is_default: false,
})

const addresses = ref<Address[]>([])
const form = reactive<AddressForm>(emptyForm())
const loading = ref(true)
const submitting = ref(false)
const busyId = ref<number | null>(null)
const errorMessage = ref('')
const loginRequired = ref(false)
const loadFailed = ref(false)
let controller: AbortController | undefined

function resetForm(): void {
  Object.assign(form, emptyForm())
}

function startEdit(address: Address): void {
  Object.assign(form, address)
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function showError(error: unknown): void {
  loginRequired.value = error instanceof AddressApiError && error.status === 403
  errorMessage.value = loginRequired.value
    ? '请先登录，再管理收货地址。'
    : error instanceof Error && error.message
      ? error.message
      : '地址操作失败，请稍后重试。'
}

async function loadAddresses(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  errorMessage.value = ''
  loginRequired.value = false
  loadFailed.value = false
  try {
    addresses.value = await fetchAddresses(controller.signal)
  } catch (error) {
    if (!controller.signal.aborted) {
      showError(error)
      loadFailed.value = !loginRequired.value
    }
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}

function formPayload(): AddressInput {
  const payload: AddressInput = {
    recipient_name: form.recipient_name,
    phone: form.phone,
    province: form.province,
    city: form.city,
    district: form.district,
    detail: form.detail,
  }
  if (form.is_default) payload.is_default = true
  return payload
}

async function submitAddress(): Promise<void> {
  submitting.value = true
  errorMessage.value = ''
  try {
    if (form.id === null) {
      await createAddress(formPayload())
    } else {
      await updateAddress(form.id, formPayload())
    }
    resetForm()
    await loadAddresses()
  } catch (error) {
    showError(error)
  } finally {
    submitting.value = false
  }
}

async function makeDefault(addressId: number): Promise<void> {
  busyId.value = addressId
  errorMessage.value = ''
  try {
    await setDefaultAddress(addressId)
    await loadAddresses()
  } catch (error) {
    showError(error)
  } finally {
    busyId.value = null
  }
}

async function removeAddress(address: Address): Promise<void> {
  if (!window.confirm(`确认删除“${address.recipient_name}”的收货地址吗？`)) return

  busyId.value = address.id
  errorMessage.value = ''
  try {
    await deleteAddress(address.id)
    if (form.id === address.id) resetForm()
    await loadAddresses()
  } catch (error) {
    showError(error)
  } finally {
    busyId.value = null
  }
}

onMounted(() => void loadAddresses())
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section aria-labelledby="addresses-heading">
    <div class="page-heading">
      <div>
        <p class="eyebrow">Minimum Address</p>
        <h1 id="addresses-heading">我的收货地址</h1>
        <p class="heading-copy">首个地址自动成为默认地址；订单快照将在下一阶段创建。</p>
      </div>
    </div>

    <div v-if="loginRequired" class="state-panel error-panel" role="alert">
      <h2>需要登录</h2>
      <p>{{ errorMessage }}</p>
      <RouterLink class="primary-button" :to="{ name: 'login', query: { redirect: '/addresses' } }">
        前往登录
      </RouterLink>
    </div>

    <template v-else>
      <form class="address-form stack-form" @submit.prevent="submitAddress">
        <div class="section-heading">
          <div>
            <p class="eyebrow">{{ form.id === null ? 'Create' : 'Edit' }}</p>
            <h2>{{ form.id === null ? '新增地址' : '编辑地址' }}</h2>
          </div>
          <button
            v-if="form.id !== null"
            class="text-button"
            type="button"
            @click="resetForm"
          >
            取消编辑
          </button>
        </div>

        <div class="address-form-grid">
          <label>
            收件人
            <input v-model.trim="form.recipient_name" maxlength="50" required />
          </label>
          <label>
            手机号
            <input
              v-model.trim="form.phone"
              inputmode="tel"
              maxlength="32"
              placeholder="13800138000"
              required
            />
          </label>
          <label>
            省
            <input v-model.trim="form.province" maxlength="50" required />
          </label>
          <label>
            市
            <input v-model.trim="form.city" maxlength="50" required />
          </label>
          <label>
            区 / 县
            <input v-model.trim="form.district" maxlength="50" required />
          </label>
          <label class="address-detail-field">
            详细地址
            <input v-model.trim="form.detail" maxlength="255" required />
          </label>
        </div>

        <label class="checkbox-label">
          <input v-model="form.is_default" type="checkbox" />
          保存后设为默认地址
        </label>
        <p v-if="errorMessage" class="inline-error" role="alert">{{ errorMessage }}</p>
        <button class="primary-button" type="submit" :disabled="submitting">
          {{ submitting ? '正在保存…' : form.id === null ? '新增地址' : '保存修改' }}
        </button>
      </form>

      <div v-if="loading" class="state-panel address-state" role="status">
        <span class="spinner" aria-hidden="true"></span>
        <h2>正在加载地址</h2>
      </div>

      <div v-else-if="loadFailed" class="state-panel address-state error-panel" role="alert">
        <h2>地址暂时无法加载</h2>
        <p>{{ errorMessage }}</p>
        <button class="primary-button" type="button" @click="loadAddresses">重新加载</button>
      </div>

      <div v-else-if="addresses.length === 0" class="state-panel address-state">
        <h2>还没有收货地址</h2>
        <p>填写上方表单后，首个地址会自动成为默认地址。</p>
      </div>

      <div v-else class="address-list">
        <article v-for="address in addresses" :key="address.id" class="address-card">
          <div>
            <div class="address-card-heading">
              <h2>{{ address.recipient_name }}</h2>
              <span v-if="address.is_default" class="stock-badge">默认地址</span>
            </div>
            <p>{{ address.phone }}</p>
            <p class="muted">
              {{ address.province }} {{ address.city }} {{ address.district }}
              {{ address.detail }}
            </p>
          </div>
          <div class="address-actions">
            <button class="secondary-button" type="button" @click="startEdit(address)">
              编辑
            </button>
            <button
              v-if="!address.is_default"
              class="secondary-button"
              type="button"
              :disabled="busyId === address.id"
              @click="makeDefault(address.id)"
            >
              设为默认
            </button>
            <button
              class="text-button danger-text"
              type="button"
              :disabled="busyId === address.id"
              @click="removeAddress(address)"
            >
              删除
            </button>
          </div>
        </article>
      </div>
    </template>
  </section>
</template>
