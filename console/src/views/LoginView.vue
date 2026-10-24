<script setup lang="ts">
import {ref} from 'vue'
import UiIcon from '../components/UiIcon.vue'

defineProps<{busy:boolean,error:string}>()
const emit=defineEmits<{login:[username:string,password:string]}>()
const username=ref('alpha.analyst')
const password=ref('')
</script>

<template>
  <main class="login-layout">
    <section class="login-story" aria-label="FinReceipts 财务证据工作台">
      <a class="login-brand" href="/" aria-label="FinReceipts 首页"><span class="brand-symbol" aria-hidden="true"><UiIcon name="receipt" :size="24"/></span>FinReceipts</a>
      <div class="story-copy"><p class="story-label">财务证据工作台</p><h2>让每一个数字，<br>都有据可查。</h2><p class="story-description">从年度财报到研究交付，<br>把结论、证据与复核放在同一张工作台。</p></div>
      <div class="document-art" aria-hidden="true">
        <svg viewBox="0 0 560 290" fill="none"><path d="M55 258H516M55 215H516M55 172H516M55 129H516M55 86H516" class="art-grid"/><rect x="101" y="30" width="263" height="225" rx="3" class="art-page-back"/><rect x="142" y="10" width="263" height="225" rx="3" class="art-page"/><path d="M168 45H277M168 57H239" class="art-heading"/><path d="M168 84H379M168 111H379M168 138H379M168 165H379M168 192H379M299 84V192" class="art-lines"/><path d="M320 98H356M320 125H367M320 152H350M320 179H361" class="art-values"/><circle cx="405" cy="218" r="34" class="art-seal"/><path d="m390 218 10 10 20-22" class="art-check"/><path d="M47 31H66M56.5 21.5V40.5M472 83H491M481.5 73.5V92.5" class="art-mark"/></svg>
      </div>
      <footer class="story-footer"><span>提交任务</span><span class="flow-line" aria-hidden="true"></span><span>核验证据</span><span class="flow-line" aria-hidden="true"></span><span>复核交付</span></footer>
    </section>
    <section class="login-form-area">
      <form class="login-form" @submit.prevent="emit('login',username.trim(),password)" :aria-busy="busy">
        <p class="form-welcome">欢迎回来</p><h1>登录 FinReceipts</h1><p class="form-description">使用组织账号，进入你的研究工作空间。</p>
        <label for="login-username">用户名</label><input id="login-username" v-model="username" name="username" autocomplete="username" required :disabled="busy" spellcheck="false" autocapitalize="none">
        <label for="login-password">密码</label><input id="login-password" v-model="password" name="password" type="password" autocomplete="current-password" required :disabled="busy">
        <p v-if="error" class="login-error" role="alert"><UiIcon name="alert" :size="17"/><span>{{error}}</span></p>
        <button class="primary enter-button" type="submit" :disabled="busy"><span>{{busy?'正在登录…':'进入工作台'}}</span></button>
        <p class="account-note">账号由组织管理员分配。<br>密码不会保存到浏览器存储。</p>
      </form>
      <p class="login-caption">财务研究 · 证据核验 · 人工复核</p>
    </section>
  </main>
</template>

<style scoped>
.login-layout{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);min-height:100svh;max-width:none;padding:0;background:var(--surface);color:var(--ink)}
.login-story{position:relative;display:flex;flex-direction:column;padding:42px clamp(36px,5.5vw,92px) 36px;background:#f3f6fa;border-right:1px solid var(--line);overflow:hidden}
.login-brand{display:inline-flex;align-items:center;gap:10px;align-self:flex-start;color:var(--ink);font-size:19px;font-weight:700;letter-spacing:-.6px;text-decoration:none}.brand-symbol{display:grid;place-items:center;width:34px;height:38px;color:var(--primary);border:1px solid #cbd6e4;border-radius:7px;background:var(--surface)}
.story-copy{margin-top:clamp(56px,10vh,116px);position:relative;z-index:1}.story-label,.form-welcome{font-size:12px;line-height:1.6;letter-spacing:.5px;font-weight:650;color:var(--muted);margin:0 0 19px}.story-copy h2{font-family:"Noto Serif SC","Source Han Serif SC","Songti SC",SimSun,serif;font-size:clamp(34px,3.3vw,51px);font-weight:500;line-height:1.52;letter-spacing:1px;margin:0 0 22px}.story-description{font-size:14px;line-height:1.95;color:var(--muted);margin:0}.document-art{width:min(100%,530px);margin:27px -12px 10px}.document-art svg{width:100%;height:auto;display:block}.art-grid{stroke:#dde5ee;stroke-width:.7}.art-page-back{fill:#eaf0f7;stroke:#cbd6e3;transform:rotate(-6deg);transform-origin:230px 160px}.art-page{fill:#fff;stroke:#bccadc;stroke-width:1.2}.art-heading{stroke:#7289a7;stroke-width:3}.art-lines{stroke:#d7e0eb;stroke-width:1}.art-values{stroke:#91a8c4;stroke-width:2}.art-seal{fill:#eaf4ef;stroke:#9dbbab}.art-check{stroke:var(--success);stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round}.art-mark{stroke:#c3cfde;stroke-width:1}.story-footer{display:flex;align-items:center;gap:14px;margin-top:auto;padding-top:18px;font-size:12px;letter-spacing:.3px;color:var(--muted);max-width:390px}.flow-line{height:1px;background:#c6d3e1;flex:1;min-width:18px}
.login-form-area{display:flex;align-items:center;justify-content:center;position:relative;padding:64px 38px 100px;min-width:0}.login-form{width:100%;max-width:356px}.login-form .form-welcome{color:var(--primary);letter-spacing:1px;margin-bottom:12px}.login-form h1{font-size:28px;font-weight:650;line-height:1.4;letter-spacing:-.8px;margin:0 0 10px}.form-description{font-size:14px;line-height:1.85;color:var(--muted);margin:0 0 32px}.login-form label{display:block;font-size:14px;line-height:1.6;font-weight:550;margin:21px 0 9px}.login-form input{height:46px;padding:11px 13px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink);width:100%;font-size:14px}.login-form input:focus-visible{border-color:var(--primary)}.enter-button{width:100%;margin-top:28px;min-height:46px;display:flex;align-items:center;justify-content:center;padding:12px 16px;font-size:14px}.account-note{font-size:12px;line-height:1.9;color:var(--muted);margin:20px 0 0}.login-error{display:flex;gap:9px;align-items:flex-start;padding:12px;margin:17px 0 0;font-size:14px;line-height:1.7;background:#fff2f0;color:var(--danger);border:1px solid #efd1cb;border-radius:5px;overflow-wrap:anywhere}.login-error svg{flex-shrink:0;margin-top:2px}.login-caption{position:absolute;bottom:27px;left:24px;right:24px;text-align:center;font-size:12px;letter-spacing:1px;color:var(--muted);margin:0}
@media(min-width:1600px){.login-story{padding-left:max(80px,calc((100vw - 1400px)/2))}.login-form-area{padding-right:max(38px,calc((100vw - 1500px)/2))}}
@media(max-width:899px){.login-layout{grid-template-columns:minmax(0,1fr)}.login-story{padding:26px 28px 28px;border-right:0;border-bottom:1px solid var(--line)}.story-copy{margin-top:34px}.story-copy h2{font-size:30px;margin-bottom:12px}.story-description{font-size:14px}.document-art{position:absolute;width:260px;right:5%;bottom:17px;opacity:.5;margin:0}.story-footer{margin-top:24px;max-width:310px}.login-form-area{padding:42px 28px 90px;min-height:520px}.login-caption{bottom:22px}.login-form input{font-size:16px}}
@media(max-width:479px){.login-story{padding:23px 24px}.story-copy{margin-top:28px}.story-copy h2{font-size:28px}.document-art{width:210px;right:-65px;bottom:22px;opacity:.35}.story-description{max-width:260px;font-size:14px}.story-footer{font-size:12px;gap:10px}.login-form-area{padding:36px 24px 80px;min-height:500px}.login-form h1{font-size:25px}.form-description{font-size:14px;margin-bottom:25px}}
</style>
