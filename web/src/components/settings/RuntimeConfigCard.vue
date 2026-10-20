<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { SystemConfig } from '@/types/system.ts'
import Button from '@/components/ui/Button.vue'
import ConfigInput from '@/components/config/ConfigInput.vue'
import { Timer, Save, Info } from 'lucide-vue-next'

const props = defineProps<{
  initialConfig: SystemConfig
}>()

const emit = defineEmits<{
  saved: []
}>()

const toast = useToastStore()

const timeoutStream = ref<number | null>(props.initialConfig.timeout_stream ?? 240)
const timeoutReplay = ref<number | null>(props.initialConfig.timeout_replay ?? 240)
const browserIdleTimeout = ref<number | null>(props.initialConfig.browser_idle_timeout ?? 900)
const browserReadyTimeout = ref<number | null>(props.initialConfig.browser_ready_timeout ?? 120)
const envOverrides = ref<string[]>(props.initialConfig.env_overrides ?? [])
const saving = ref(false)

watch(
  () => props.initialConfig,
  (cfg) => {
    timeoutStream.value = cfg.timeout_stream ?? 240
    timeoutReplay.value = cfg.timeout_replay ?? 240
    browserIdleTimeout.value = cfg.browser_idle_timeout ?? 900
    browserReadyTimeout.value = cfg.browser_ready_timeout ?? 120
    envOverrides.value = cfg.env_overrides ?? []
  },
  { deep: true },
)

const ENV_NAMES: Record<string, string> = {
  timeout_stream: 'AISTUDIO_TIMEOUT_STREAM',
  timeout_replay: 'AISTUDIO_TIMEOUT_REPLAY',
  browser_idle_timeout: 'AISTUDIO_BROWSER_IDLE_TIMEOUT',
  browser_ready_timeout: 'AISTUDIO_BROWSER_READY_TIMEOUT',
}

const anyEnvOverride = computed(() => envOverrides.value.length > 0)

function envHint(key: string): string {
  return envOverrides.value.includes(key)
    ? `环境变量 ${ENV_NAMES[key]} 已设置，重启后以环境变量为准`
    : ''
}

function toNumber(raw: string | number | null): number | null {
  if (raw === null || raw === '') return null
  return Number(raw)
}

function validate(name: string, value: number | null): string | null {
  if (value === null) return `${name}不能为空`
  if (!Number.isInteger(value) || value < 0) return `${name}必须是不小于 0 的整数`
  if (value > 86400) return `${name}不能超过 86400 秒`
  return null
}

async function handleSave() {
  if (browserReadyTimeout.value === null || !Number.isInteger(browserReadyTimeout.value)
    || browserReadyTimeout.value < 30 || browserReadyTimeout.value > 300) {
    toast.error('浏览器准备上限必须是 30–300 秒的整数')
    return
  }
  const fields: Array<[string, number | null]> = [
    ['流式请求静默超时', timeoutStream.value],
    ['非流式请求超时', timeoutReplay.value],
    ['浏览器空闲回收', browserIdleTimeout.value],
  ]
  for (const [name, value] of fields) {
    const error = validate(name, value)
    if (error) {
      toast.error(error)
      return
    }
  }

  saving.value = true
  try {
    const res = await systemApi.updateRuntimeConfig({
      timeout_stream: timeoutStream.value,
      timeout_replay: timeoutReplay.value,
      browser_idle_timeout: browserIdleTimeout.value,
      browser_ready_timeout: browserReadyTimeout.value,
    })
    timeoutStream.value = res.timeout_stream
    timeoutReplay.value = res.timeout_replay
    browserIdleTimeout.value = res.browser_idle_timeout
    browserReadyTimeout.value = res.browser_ready_timeout
    envOverrides.value = res.env_overrides
    toast.success('运行参数已保存并即时生效')
    emit('saved')
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '保存运行参数失败'
    toast.error(msg)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-6 shadow-xs space-y-5">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div class="flex items-center gap-2.5">
        <div class="p-2 rounded-xl bg-sky-50 text-sky-600 border border-sky-100">
          <Timer class="w-5 h-5" />
        </div>
        <div>
          <h3 class="font-semibold text-gray-900 text-sm">
            运行参数
          </h3>
          <p class="text-[11px] text-gray-400 mt-0.5">
            单位均为秒；保存后立即生效。浏览器准备上限为 30–300 秒；其他参数填 0 表示不超时或不自动回收。
          </p>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          :loading="saving"
          @click="handleSave"
        >
          <Save class="w-3.5 h-3.5" />
          <span>保存运行参数</span>
        </Button>
      </div>
    </div>

    <div
      v-if="anyEnvOverride"
      class="p-3.5 bg-blue-50/80 border border-blue-200 rounded-xl flex items-start gap-2.5 text-xs text-blue-900 leading-relaxed"
    >
      <Info class="w-4 h-4 text-blue-600 shrink-0 mt-0.5" />
      <div>
        <span class="font-bold">环境变量覆盖提示：</span>
        部分运行参数由环境变量显式指定。此处保存的值会即时生效，但下次启动时环境变量仍优先；如需长期以界面为准，请移除对应环境变量。
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <ConfigInput
          :model-value="browserReadyTimeout"
          type="number"
          label="浏览器准备上限"
          :description="envHint('browser_ready_timeout') || '同一次请求的启动和恢复累计等待上限，不计正常生成时间'"
          placeholder="120"
          @update:model-value="browserReadyTimeout = toNumber($event)"
        />
      </div>
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <ConfigInput
          :model-value="timeoutStream"
          type="number"
          label="流式请求静默超时"
          :description="envHint('timeout_stream') || '等待上游响应与数据块，收到数据即重新计时'"
          placeholder="240"
          @update:model-value="timeoutStream = toNumber($event)"
        />
        <div
          v-if="timeoutStream === 0"
          class="text-[11px] font-semibold text-sky-700"
        >
          当前：不超时
        </div>
      </div>

      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <ConfigInput
          :model-value="timeoutReplay"
          type="number"
          label="非流式请求超时"
          :description="envHint('timeout_replay') || 'generateContent 与生图整次请求的等待上限'"
          placeholder="240"
          @update:model-value="timeoutReplay = toNumber($event)"
        />
        <div
          v-if="timeoutReplay === 0"
          class="text-[11px] font-semibold text-sky-700"
        >
          当前：不超时
        </div>
      </div>

      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <ConfigInput
          :model-value="browserIdleTimeout"
          type="number"
          label="浏览器空闲回收"
          :description="envHint('browser_idle_timeout') || '无请求时关闭后台浏览器释放内存'"
          placeholder="900"
          @update:model-value="browserIdleTimeout = toNumber($event)"
        />
        <div
          v-if="browserIdleTimeout === 0"
          class="text-[11px] font-semibold text-sky-700"
        >
          当前：不自动回收
        </div>
      </div>
    </div>
  </div>
</template>
