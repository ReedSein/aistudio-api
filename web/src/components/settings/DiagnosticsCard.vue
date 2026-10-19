<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Download, RefreshCw, AlertCircle } from 'lucide-vue-next'
import { systemApi } from '@/api/system.ts'
import type { DiagnosticsStatus } from '@/types/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import Button from '@/components/ui/Button.vue'

const toast = useToastStore()
const status = ref<DiagnosticsStatus | null>(null)
const loading = ref(false)
const exporting = ref(false)
const statusError = ref('')
let downloadUrl: string | null = null

function sizeLabel(bytes: number | null): string {
  return bytes === null ? '未知' : `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

async function loadStatus() {
  loading.value = true
  statusError.value = ''
  try {
    status.value = await systemApi.getDiagnostics()
  } catch (error: unknown) {
    statusError.value = error instanceof Error ? error.message : '无法读取日志状态'
  } finally {
    loading.value = false
  }
}

async function exportLogs() {
  exporting.value = true
  try {
    const blob = await systemApi.exportDiagnostics()
    if (downloadUrl) URL.revokeObjectURL(downloadUrl)
    downloadUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = `aistudio-diagnostics-${new Date().toISOString().replaceAll(':', '-')}.tar.gz`
    document.body.appendChild(link)
    link.click()
    link.remove()
    toast.success('诊断包已生成，浏览器将开始下载')
    await loadStatus()
  } catch (error: unknown) {
    toast.error(error instanceof Error ? error.message : '诊断包导出失败')
  } finally {
    exporting.value = false
  }
}

onMounted(() => { void loadStatus() })
onUnmounted(() => { if (downloadUrl) URL.revokeObjectURL(downloadUrl) })
</script>

<template>
  <div class="rounded-2xl border border-blue-200 bg-blue-50/50 p-4 space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h4 class="text-sm font-semibold text-gray-900">
        问题排查与日志导出
      </h4>
      <div class="flex gap-2">
        <Button
          variant="secondary"
          size="sm"
          :loading="loading"
          @click="loadStatus"
        >
          <RefreshCw class="w-3.5 h-3.5" /> 刷新状态
        </Button>
        <Button
          variant="primary"
          size="sm"
          :loading="exporting"
          @click="exportLogs"
        >
          <Download class="w-3.5 h-3.5" /> 导出诊断包
        </Button>
      </div>
    </div>
    <p class="text-xs leading-relaxed text-gray-600">
      运行日志和完整请求、回复原文持续保存，静默启动也会记录。导出包含对话原文，Cookie 和认证密钥会脱敏，请只发送给信任的排错人员。
    </p>
    <div
      v-if="status"
      class="space-y-1 text-xs text-gray-600"
    >
      <p>已保留 {{ status.file_count ?? '未知' }} 个日志文件，共 {{ sizeLabel(status.total_bytes) }}；保留最近 {{ status.retention_days }} 天，上限 {{ sizeLabel(status.max_bytes) }}。</p>
      <p class="break-all">
        日志位置：{{ status.directory }}
      </p>
      <p v-if="status.oldest_file_time">
        最早保留文件时间：{{ new Date(status.oldest_file_time).toLocaleString() }}
      </p>
    </div>
    <p
      v-if="statusError || status?.collection_error"
      class="flex items-start gap-2 text-xs text-amber-800"
    >
      <AlertCircle class="w-4 h-4 shrink-0" />
      {{ statusError || `日志采集存在错误：${status?.collection_error}，导出的证据可能不完整。` }}
    </p>
    <p class="text-xs text-gray-500 leading-relaxed">
      包内会说明覆盖时段、清理和缺失情况。服务起不来时，可在 Termux 使用 aistudio --export-logs，或运行项目内 scripts/export-diagnostics.sh。
    </p>
  </div>
</template>
