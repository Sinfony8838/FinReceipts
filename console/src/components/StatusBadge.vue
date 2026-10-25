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
.status-badge { display: inline-flex; align-items: center; gap: 5px; width: max-content; white-space: nowrap; padding: 2px 6px; border: 1px solid #e4e7e0; border-radius: 4px; font-size: 0.875rem; line-height: 1.5; font-weight: 500; color: var(--muted, #626960); background: #f7f8f5; }
.status-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex: none; }
.success { color: var(--success); background: #edf4e9; border-color: #dce7d6; }
.danger { color: var(--danger); background: #fdf0ee; border-color: #f0dedb; }
.warning { color: var(--warning); background: #fcf5e7; border-color: #eee3ca; }
.active { color: #3d5d83; background: #eff3f8; border-color: #dde5f0; }
</style>
