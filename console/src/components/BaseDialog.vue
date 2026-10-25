<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, onUnmounted, ref } from 'vue'
import UiIcon from './UiIcon.vue'
withDefaults(defineProps<{ title: string; drawer?: boolean }>(), { drawer: false })
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
let trigger: HTMLElement | null = null
let disposed = false
function focusableElements() {
  if (!dialog.value) return []
  return Array.from(dialog.value.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, summary, [tabindex]'))
    .filter(element => !element.matches(':disabled') && element.tabIndex >= 0 && !element.closest('[inert]')
      && element.getClientRects().length > 0 && !['hidden', 'collapse'].includes(getComputedStyle(element).visibility))
}
function trapTab(event: KeyboardEvent) {
  const element = dialog.value
  if (event.key !== 'Tab' || !element?.open) return
  const focusable = focusableElements()
  if (!focusable.length) { event.preventDefault(); element.focus(); return }
  const first = focusable[0]!, last = focusable[focusable.length - 1]!
  const active = document.activeElement
  if (!active || !element.contains(active)) { event.preventDefault(); first.focus(); return }
  if (event.shiftKey && active === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus() }
}
onMounted(async () => {
  trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  if (disposed || !dialog.value?.isConnected) return
  dialog.value.showModal()
  document.addEventListener('keydown', trapTab, true)
  ;(focusableElements()[0] || dialog.value).focus({ preventScroll: true })
})
onBeforeUnmount(() => {
  disposed = true
  document.removeEventListener('keydown', trapTab, true)
  dialog.value?.close()
})
onUnmounted(() => {
  const original = trigger?.isConnected && trigger !== document.body && !trigger.matches(':disabled') ? trigger : null
  const target = original || document.querySelector<HTMLElement>('.main-content')
  target?.focus({ preventScroll: true })
})
function backdrop(event: MouseEvent) {
  const element = dialog.value
  if (!element || event.target !== element) return
  const rect = element.getBoundingClientRect()
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) emit('close')
}
</script>

<template>
  <dialog ref="dialog" :class="['dialog', { evidence: drawer }]" :aria-label="title" aria-modal="true" tabindex="-1" @cancel.prevent="emit('close')" @click="backdrop">
    <div class="dialog-header"><slot name="header"><h2>{{ title }}</h2></slot><button type="button" class="close-button" aria-label="关闭" @click="emit('close')"><UiIcon name="close" :size="19" /></button></div>
    <div class="dialog-content"><slot /></div>
  </dialog>
</template>

<style scoped>
.dialog { position: fixed; padding: 0; width: min(640px, calc(100vw - 40px)); max-width: none; max-height: calc(100dvh - 64px); border: 1px solid var(--line, #e5e7e3); border-radius: 6px; background: var(--surface, #fff); color: var(--ink, #202321); box-shadow: 0 12px 48px #20232124; overflow: auto; }
.dialog::backdrop { background: #20232166; }
.dialog.evidence { inset: 0 0 0 auto; margin: 0; width: min(1040px, calc(100vw - 52px)); max-height: 100dvh; height: 100dvh; border-radius: 0; border-width: 0 0 0 1px; }
.dialog-header { position: sticky; top: 0; z-index: 2; display: flex; justify-content: space-between; align-items: center; gap: 20px; padding: 18px 22px; border-bottom: 1px solid var(--line, #e5e7e3); background: var(--surface, #fff); }
h2 { margin: 0; color: var(--ink, #202321); font-size: 1.125rem; line-height: 1.5; font-weight: 650; }
.close-button { display: grid; place-items: center; flex: none; width: 36px; min-height: 36px; height: 36px; padding: 0; border: 1px solid var(--line, #e5e7e3); border-radius: 6px; background: var(--surface, #fff); color: var(--muted, #666b66); cursor: pointer; }
.close-button:hover { color: var(--ink, #202321); background: var(--canvas, #f7f7f5); }
.dialog-content { padding: 20px 22px 24px; }
@media (max-width: 680px) { .dialog { max-height: calc(100dvh - 24px); width: calc(100vw - 24px); } .dialog.evidence { width: 100%; } .dialog-header { padding: 18px; } .dialog-content { padding: 20px 18px; } .close-button { width: 44px; min-height: 44px; height: 44px; } h2 { font-size: 1.125rem; } }
</style>
