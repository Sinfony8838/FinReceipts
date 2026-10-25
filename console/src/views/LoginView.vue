<script setup lang="ts">
import {ref} from 'vue'
import UiIcon from '../components/UiIcon.vue'
import TextSizeControl from '../components/TextSizeControl.vue'
defineProps<{busy:boolean,error:string}>()
const emit=defineEmits<{login:[username:string,password:string]}>()
const username=ref('alpha.analyst')
const password=ref('')
</script>

<template>
  <main class="login-layout">
    <header class="login-header">
      <a class="login-brand" href="/" aria-label="FinReceipts 首页"><UiIcon name="receipt" :size="23"/><span>FinReceipts</span></a>
      <span class="product-label">财报核验工作台</span>
      <TextSizeControl class="login-text-size" />
    </header>
    <section class="login-form-area">
      <form class="login-form" @submit.prevent="emit('login',username.trim(),password)" :aria-busy="busy">
        <h1>登录 FinReceipts</h1><p class="form-description">使用组织账号进入工作空间。</p>
        <label for="login-username">用户名</label><input id="login-username" v-model="username" name="username" autocomplete="username" required :disabled="busy" spellcheck="false" autocapitalize="none">
        <label for="login-password">密码</label><input id="login-password" v-model="password" name="password" type="password" autocomplete="current-password" required :disabled="busy">
        <p v-if="error" class="login-error" role="alert"><UiIcon name="alert" :size="16"/><span>{{error}}</span></p>
        <button class="primary enter-button" type="submit" :disabled="busy">{{busy?'正在登录…':'进入工作台'}}<UiIcon name="arrow-right" :size="16"/></button>
        <p class="account-note">账号由组织管理员分配，密码不会保存到浏览器存储。</p>
      </form>
    </section>
    <footer class="login-footer"><span>FinReceipts</span><span>财报研究与证据复核</span></footer>
  </main>
</template>

<style scoped>
.login-layout{display:flex;flex-direction:column;min-height:100svh;background:var(--surface);color:var(--ink)}
.login-header{display:flex;align-items:center;gap:20px;padding:24px 36px;border-bottom:1px solid var(--line)}
.login-brand{display:inline-flex;align-items:center;gap:9px;color:var(--ink);font-size:1.125rem;font-weight:650;letter-spacing:-.5px;text-decoration:none}.login-brand svg{color:var(--primary)}.product-label{padding-left:20px;border-left:1px solid var(--line);font-size:0.875rem;color:var(--muted)}
.login-form-area{flex:1;display:flex;align-items:flex-start;justify-content:center;padding:clamp(68px,15vh,150px) 24px 80px;min-width:0}.login-form{width:100%;max-width:352px}.login-form h1{margin:0 0 9px;font-size:1.5rem;font-weight:600;line-height:1.4;letter-spacing:-.5px}.form-description{font-size:0.9375rem;line-height:1.8;color:var(--muted);margin:0 0 28px}.login-form label{display:block;font-size:0.9375rem;line-height:1.6;font-weight:500;margin:18px 0 7px}.login-form input{height:42px;padding:9px 11px;border:1px solid #cdd1cb;border-radius:5px;background:var(--surface);color:var(--ink);width:100%;font-size:0.9375rem}.login-form input:focus-visible{border-color:var(--primary)}.enter-button{width:100%;margin-top:24px;min-height:42px;justify-content:center;position:relative;font-size:0.9375rem}.enter-button svg{position:absolute;right:12px}.account-note{font-size:0.875rem;line-height:1.8;color:var(--muted);margin:18px 0 0}.login-error{display:flex;gap:8px;align-items:flex-start; padding:10px;margin:16px 0 0;font-size:0.9375rem;line-height:1.7;background:#fff4f2;color:var(--danger);border:1px solid #efd9d3;border-radius:5px;overflow-wrap:anywhere}.login-error svg{flex-shrink:0;margin-top:2px}.login-footer{display:flex;justify-content:space-between;gap:18px;padding:20px 36px;font-size:0.875rem;color:var(--muted);border-top:1px solid var(--line)}
@media(max-width:767px){.login-header{padding:20px 24px;gap:12px}.login-brand{font-size:1.0625rem}.product-label{padding-left:12px;font-size:0.875rem}.login-form-area{padding:64px 24px 80px}.login-form input{min-height:44px;font-size:1rem}.enter-button{min-height:44px;font-size:0.9375rem}.login-footer{padding:20px 24px}.login-form h1{font-size:1.4375rem}}
.login-header { flex-wrap: wrap; }
.login-text-size { margin-left: auto; }
.login-form input { height: auto; min-height: 44px; }
.login-form { max-width: 390px; }
@media(max-width:767px){.product-label{display:none}}
@media(max-width:359px){.login-footer{gap:8px;font-size:0.8125rem}}
</style>
