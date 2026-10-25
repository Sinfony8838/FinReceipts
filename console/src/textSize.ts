import { ref } from 'vue'

export const textSizes = [
  { value: 'standard', label: '标准' },
  { value: 'large', label: '较大' },
  { value: 'xlarge', label: '大号' },
] as const
type TextSize = typeof textSizes[number]['value']
const storageKey = 'fr:ui:text-size'
export const textSize = ref<TextSize>('standard')

function valid(value: string | null): value is TextSize {
  return textSizes.some(size => size.value === value)
}
function apply(value: TextSize) {
  textSize.value = value
  document.documentElement.dataset.textSize = value
}
export function initializeTextSize() {
  let stored: string | null = null
  try { stored = localStorage.getItem(storageKey) } catch { /* Keep the default when storage is unavailable. */ }
  apply(valid(stored) ? stored : 'standard')
}
export function setTextSize(value: string) {
  if (!valid(value)) return
  apply(value)
  try { localStorage.setItem(storageKey, value) } catch { /* The selection still works for this page. */ }
}
