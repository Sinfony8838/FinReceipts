<script setup lang="ts">
import { textSize, textSizes, setTextSize } from '../textSize'
function change(event: Event) { setTextSize((event.target as HTMLSelectElement).value) }
</script>

<template>
  <label class="text-size-control"><span aria-hidden="true">Aa</span><select aria-label="页面字号" :value="textSize" @change="change"><option v-for="size in textSizes" :key="size.value" :value="size.value">字号 · {{size.label}}</option></select></label>
</template>

<style scoped>
.text-size-control { position: relative; display: inline-flex; align-items: center; flex-shrink: 0; color: var(--muted); }
.text-size-control > span { position: absolute; left: 10px; font-size: .875rem; pointer-events: none; }
.text-size-control select { width: auto; min-height: 38px; padding: 7px 24px 7px 35px; border-color: var(--line); font-size: .875rem; line-height: 1.5; cursor: pointer; }
@media (max-width: 767px) { .text-size-control select { min-height: 44px; } }
</style>
