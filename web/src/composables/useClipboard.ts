import { ref } from 'vue'
import { useToastStore } from '@/stores/toast.ts'

let timer: ReturnType<typeof setTimeout> | undefined

function legacyCopy(text: string): boolean {
  if (typeof document === 'undefined' || !document.body) return false
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '-9999px'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  try {
    textarea.focus()
    textarea.select()
    textarea.setSelectionRange(0, text.length)
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(textarea)
  }
}

/**
 * 复制文本到剪贴板，并在 2 秒内记录“已复制”的键；
 * 同一键重复触发时重置计时。
 */
export function useClipboard() {
  const toast = useToastStore()
  const copied = ref<string | number | null>(null)

  async function copy(text: string, key: string | number, message = '已复制到剪贴板') {
    let ok: boolean
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text)
        ok = true
      } catch {
        ok = legacyCopy(text)
      }
    } else {
      ok = legacyCopy(text)
    }
    if (!ok) {
      toast.error('复制失败，浏览器可能拒绝了剪贴板权限')
      return
    }
    copied.value = key
    toast.success(message)
    clearTimeout(timer)
    timer = setTimeout(() => {
      if (copied.value === key) copied.value = null
    }, 2000)
  }

  return { copied, copy }
}
