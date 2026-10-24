<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 18 })
const paths: Record<string, string> = {
  batches: 'M4 3h6v8H4z M14 3h6v5h-6z M14 12h6v9h-6z M4 15h6v6H4z',
  review: 'M9 4H5a1 1 0 0 0-1 1v15h16V5a1 1 0 0 0-1-1h-4 M9 3h6v4H9z M8 13l3 3 5-6',
  quota: 'M4 20h16 M7 16v-5 M12 16V4 M17 16V8',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M20 12l2-1-2-4-2 1-2-1V4H8v3L6 8 4 7l-2 4 2 1v2l-2 1 2 4 2-1 2 1v2h8v-2l2-1 2 1 2-4-2-1z',
  plus: 'M12 5v14 M5 12h14',
  close: 'M6 6l12 12 M18 6 6 18',
  logout: 'M9 4H4v16h5 M13 8l4 4-4 4 M8 12h12',
  file: 'M14 3H5v18h14V8z M14 3v6h5 M8 13h8 M8 17h5',
  check: 'M5 12l4 4L19 6',
  chevron: 'M9 5l7 7-7 7',
  download: 'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',
  lock: 'M6 10h12v11H6z M8 10V7a4 4 0 0 1 8 0v3 M12 15v2',
  search: 'M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15 M16 16l5 5',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2',
  external: 'M14 3h7v7 M21 3 11 13 M10 5H4v15h15v-6',
  alert: 'M12 3 2 21h20L12 3z M12 9v5 M12 17h.01',
  refresh: 'M20 8a8 8 0 1 0 0 8 M20 3v5h-5',
  menu: 'M4 6h16 M4 12h16 M4 18h16',
  receipt: 'M5 3h14v18l-3-2-4 2-4-2-3 2V3z M9 7h6 M9 11h6 M9 15h3',
  'arrow-right': 'M5 12h14 M13 6l6 6-6 6',
  layers: 'm12 3 10 6-10 6L2 9z M2 13l10 6 10-6 M2 17l10 6 10-6',
  'chevron-down': 'm5 9 7 7 7-7',
  'arrow-up-right': 'M7 17 17 7 M7 7h10v10',
  'check-circle': 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M8 12l3 3 5-6',
}
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path :d="paths[name] || paths.file" /></svg>
</template>

<style scoped>
svg { display: inline-block; flex: none; vertical-align: middle; }
</style>
