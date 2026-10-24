<script setup lang="ts">
import { computed } from 'vue'
import { text } from '../format'
const props = defineProps<{ value: string }>()
const tone = computed(() => {
  if (['SUPPORTED', 'ACCEPTED'].includes(props.value)) return 'success'
  if (['FAILED', 'CONTRADICTED', 'REJECTED'].includes(props.value)) return 'danger'
  if (['UNKNOWN', 'UNSUPPORTED'].includes(props.value)) return 'warning'
  if (['RUNNING', 'READY', 'MODEL', 'VERIFY'].includes(props.value)) return 'active'
  return 'neutral'
})
</script>

<template><span :class="['status-badge', value, tone]"><span class="status-dot" />{{ text(value) }}</span></template>

<style scoped>
.status-badge { display: inline-flex; align-items: center; gap: 7px; width: max-content; white-space: nowrap; padding: 5px 9px; border-radius: 5px; font-size: 12px; line-height: 1.5; font-weight: 600; color: var(--muted, #657386); background: var(--canvas, #f4f6f9); }
.status-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex: none; }
.success { color: var(--success, #25826c); background: #eaf5ef; }
.danger { color: var(--danger, #bd4e4e); background: #fbefee; }
.warning { color: var(--warning, #9b691b); background: #faf2e3; }
.active { color: var(--primary, #325ed1); background: #eef2fc; }
</style>
