<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useClipboard } from '@/composables/useClipboard.ts'
import Button from '@/components/ui/Button.vue'
import {
  BookOpen,
  Check,
  Copy,
  Network,
  Sparkles,
  Wrench,
  AlertTriangle,
  Sliders,
  Users,
  CheckCircle2,
} from 'lucide-vue-next'

type ClientId = 'sillytavern' | 'luker'
type ProtocolId = 'gemini' | 'openai'
type HostMode = 'local' | 'lan'

interface StepField {
  key: string
  label: string
  selector: string
  value: string
  copyable?: boolean
  note: string
}

const router = useRouter()
const { copied, copy } = useClipboard()

const activeClient = ref<ClientId>('sillytavern')
const activeProtocol = ref<ProtocolId>('gemini')
const hostMode = ref<HostMode>('local')

const currentPort = computed(() => {
  if (typeof window !== 'undefined' && window.location.port) {
    return window.location.port
  }
  return '11451'
})

const baseHost = computed(() =>
  hostMode.value === 'local' ? '127.0.0.1' : '192.168.1.100',
)

const geminiRootUrl = computed(() => `http://${baseHost.value}:${currentPort.value}`)
const openaiBaseUrl = computed(() => `http://${baseHost.value}:${currentPort.value}/v1`)
const placeholderKey = 'your-api-key'

const clientMeta = computed(() => {
  if (activeClient.value === 'sillytavern') {
    return {
      name: '酒馆 (SillyTavern)',
      badge: '源码核验 SHA: 06bde93',
      summary:
        '在顶部「插头 (API Connections)」面板配置。原生 Google AI Studio 分支通过反向代理地址直连本服务 /v1beta；Custom 分支通过 /v1 连接 OpenAI 兼容接口。',
    }
  }
  return {
    name: 'Luker',
    badge: '实测 SHA: f18f73c · 上游核验: 3c9294f',
    summary:
      '基于 SillyTavern 的衍生客户端，连接面板位于顶部「API Connections」。已在隔离真实环境中完成原生 Gemini 与 OpenAI 兼容的普通/流式双轮收发验收。',
  }
})

const protocolMeta = computed(() => {
  if (activeProtocol.value === 'gemini') {
    return {
      title: '原生 Gemini 接法（推荐）',
      subtitle:
        '客户端直接按 Google AI Studio (v1beta) 协议发包，完整保留原生系统指令、内嵌多模态、思考配置与 thoughtSignature。',
      endpointLabel: '反向代理根地址（不要加 /v1 或 /v1beta）',
      endpointValue: geminiRootUrl.value,
    }
  }
  return {
    title: 'OpenAI 兼容接法 (/v1)',
    subtitle:
      '客户端按 OpenAI Chat Completions 协议发包，本服务在接入层转换为 Gemini 请求并复用同一套账号轮换与浏览器执行链。',
    endpointLabel: 'OpenAI 自定义 Base URL（填到 /v1 为止）',
    endpointValue: openaiBaseUrl.value,
  }
})

const connectionFields = computed<StepField[]>(() => {
  const clientName = activeClient.value === 'sillytavern' ? 'SillyTavern' : 'Luker'
  if (activeProtocol.value === 'gemini') {
    return [
      {
        key: 'api-type',
        label: '1. API 类型 (API)',
        selector: '#main_api',
        value: 'Chat Completion',
        note: `打开 ${clientName} 顶部插头图标，将主 API 类型设为 Chat Completion。`,
      },
      {
        key: 'source',
        label: '2. 来源 (Chat Completion Source)',
        selector: '#chat_completion_source',
        value: 'Google AI Studio',
        note: '对应内部值 makersuite。客户端会自动在根地址后追加 /v1beta/models/{model}:generateContent 或 :streamGenerateContent。',
      },
      {
        key: 'proxy-url',
        label: '3. 反向代理地址 (Reverse Proxy)',
        selector: '#openai_reverse_proxy',
        value: geminiRootUrl.value,
        copyable: true,
        note: '只填服务根地址，切勿附加 /v1、/v1beta 或 /models，否则路径会重复变成 404。',
      },
      {
        key: 'proxy-key',
        label: '4. 反向代理密码 (Reverse Proxy Key)',
        selector: '#openai_proxy_access_key',
        value: placeholderKey,
        copyable: true,
        note: '填写本服务配置的 API Key（非 Google 官方 AI Studio Key）。若本服务未开启 API Key 鉴权，也建议填入任意非空占位符以确保客户端走反向代理分支。',
      },
      {
        key: 'model',
        label: '5. 连接并选择模型 (Google AI Studio Model)',
        selector: '#api_button_openai / #model_google_select',
        value: 'gemini-3.8-flash',
        copyable: true,
        note: '点击「Connect / 连接」拉取模型目录后，在下拉框选择如 gemini-3.8-flash 或 gemini-3.1-flash-lite。',
      },
      {
        key: 'stream',
        label: '6. 流式传输 (Streaming)',
        selector: '#stream_toggle',
        value: '开启或关闭均支持',
        note: '关闭时走 :generateContent 一次性返回；开启时走 :streamGenerateContent (SSE) 增量输出。',
      },
    ]
  }

  return [
    {
      key: 'api-type',
      label: '1. API 类型 (API)',
      selector: '#main_api',
      value: 'Chat Completion',
      note: `打开 ${clientName} 顶部插头图标，将主 API 类型设为 Chat Completion。`,
    },
    {
      key: 'source',
      label: '2. 来源 (Chat Completion Source)',
      selector: '#chat_completion_source',
      value: 'Custom (OpenAI-compatible)',
      note: '对应内部值 custom。客户端会自动在 Base URL 后追加 /chat/completions 与 /models。',
    },
    {
      key: 'custom-url',
      label: '3. 自定义端点 (Custom Endpoint / Base URL)',
      selector: '#custom_api_url_text',
      value: openaiBaseUrl.value,
      copyable: true,
      note: '必须以 /v1 结尾，不要手动追加 /chat/completions。',
    },
    {
      key: 'custom-key',
      label: '4. 自定义密钥 (Custom API Key)',
      selector: '#api_key_custom',
      value: placeholderKey,
      copyable: true,
      note: '填写本服务配置的 API Key（以 Authorization: Bearer 发送）；未启用 API Key 鉴权时可留空或填占位符。',
    },
    {
      key: 'custom-model',
      label: '5. 模型选择 (Available Models / Custom Model ID)',
      selector: '#api_button_openai / #custom_model_id',
      value: 'gemini-3.8-flash',
      copyable: true,
      note: '点击「Connect / 连接」从 /v1/models 拉取列表，或直接填写 Gemini 模型原名（不要带 models/ 前缀）。',
    },
    {
      key: 'stream',
      label: '6. 流式与思考 (Streaming / Reasoning Effort)',
      selector: '#stream_toggle / #openai_reasoning_effort',
      value: '支持普通与流式；思考转为 thinkingLevel',
      note: '模型思考过程通过 reasoning_content 字段独立返回，不会混入正文；工具调用签名通过 call_gemini_... 编号无状态往返。',
    },
  ]
})

const parameterRows = [
  {
    group: '基础采样与停止词',
    fields: 'temperature, top_p, top_k, presence_penalty, frequency_penalty, stop',
    behavior: '转换为 Gemini generationConfig 对应字段；显式 0 保留，stop 字符串自动转为单元素数组。超出范围的数值会被记录并忽略。',
  },
  {
    group: '最大输出 Token',
    fields: 'max_completion_tokens, max_tokens',
    behavior: '转换为 maxOutputTokens；两者同时提供时 max_completion_tokens 优先。',
  },
  {
    group: '思考强度与推理扩展',
    fields: 'reasoning_effort, reasoning.effort, thinking.thinkingLevel',
    behavior: 'minimal / low / medium / high（以及 xhigh / max → HIGH）映射为 thinkingConfig.thinkingLevel；回复在 message.reasoning_content 或 delta.reasoning_content 中单独承载思考。',
  },
  {
    group: '工具调用与历史签名',
    fields: 'tools, tool_choice, functions, function_call',
    behavior: '函数声明与 auto / none / required / 指定函数名均可转换；返回的 tool_calls[].id 采用 call_gemini_... 无状态编码保存原生调用 ID 与 thoughtSignature，客户端按常规 OpenAI 历史回传即可通过多轮校验。',
  },
  {
    group: '结构化输出',
    fields: 'response_format',
    behavior: 'json_object 与 json_schema 转为 responseMimeType="application/json" 及 responseSchema。',
  },
  {
    group: '多模态附件与远程图片',
    fields: 'image_url, input_image, input_audio, file / input_file',
    behavior: '支持 Base64 Data URL 内嵌图片（PNG/JPEG/GIF/WebP）、音频（WAV/MP3）、PDF 与 UTF-8 文本；HTTP(S) 图片 URL 仅允许无凭据、无重定向的公网地址，单附件上限 20 MiB、单次请求附件总计上限 32 MiB（最多 32 个）。无法读取或不支持的媒体直接返回 400。',
  },
  {
    group: '明确忽略的调节项',
    fields: 'n (>1), seed, logit_bias, logprobs, top_logprobs, user, store, service_tier',
    behavior: 'n 归一为单候选（不额外发起多次生成）；其余无 AI Studio 等价能力的调节项按规则忽略并在诊断日志记录 openai_compatibility 事件。',
  },
]

const troubleshootingItems = [
  {
    symptom: '点击连接或发消息返回 404 Not Found',
    cause: '地址后缀与协议不匹配：在原生 Gemini 反向代理里多填了 /v1 或 /v1beta，或在 OpenAI 自定义端点里漏掉了 /v1、多填了 /chat/completions。',
    fix: '原生 Google AI Studio 反向代理只填根地址（如 http://127.0.0.1:11451）；OpenAI Custom Endpoint 填到 /v1（如 http://127.0.0.1:11451/v1）。',
  },
  {
    symptom: '返回 401 Unauthorized 或 API key not valid',
    cause: '1) 本服务启用了 API Key 鉴权但客户端未填或填错；2) 在原生 Gemini 模式下把密钥填进了上方的 Google API Key 输入框，而没有填进 Reverse Proxy Key（导致客户端直连 Google 官方并拿服务密钥去鉴权）。',
    fix: '前往控制台「系统与模型配置」核对本服务 API Key；原生 Gemini 模式下务必填在 Reverse Proxy Key 输入框。',
  },
  {
    symptom: '跨设备连接超时，或模型列表一直为空',
    cause: '把地址填成了 127.0.0.1，但酒馆 / Luker 后台其实运行在另一台设备上；或者手机与电脑不在同一局域网。',
    fix: '服务地址由「酒馆 / Luker 后台运行在哪台设备」决定。跨设备时请填写运行 aistudio-api 的手机/主机局域网 IP（如 http://192.168.1.x:11451），并确认防火墙放行。',
  },
  {
    symptom: '发消息返回 400 invalid_request_error',
    cause: '请求中包含无法送达模型的正文内容：例如远程图片 URL 指向私网/重定向/超过 20 MiB、附件格式不支持，或历史消息中的 tool_calls 缺少配对的 tool 结果。',
    fix: '本服务不会静默丢弃正文或附件。请检查报错返回中的 param 字段定位问题消息，或改用内嵌 Base64 图片。',
  },
  {
    symptom: '生成中途中断或返回 502 / 503',
    cause: '当前没有就绪的 Google AI Studio 账号、账号 Cookie 失效，或上游流式连接在完成标记到达前中断（流中途不会静默补假 [DONE] 或重放整段生成）。',
    fix: '先到「账号管理」检查是否有可用账号；若问题持续，前往「系统与模型配置」导出脱敏诊断包排查。',
  },
]

function handleCopy(text: string, key: string, label: string) {
  void copy(text, key, `已复制${label}`)
}
</script>

<template>
  <div class="space-y-6">
    <!-- Top Header -->
    <div class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
      <div>
        <h2 class="text-xl font-bold text-gray-900 tracking-tight flex items-center gap-2">
          <BookOpen class="w-5 h-5 text-brand-600" />
          <span>客户端使用教程</span>
        </h2>
        <p class="text-xs text-gray-500 mt-1">
          面向酒馆 (SillyTavern) 与 Luker 的双协议接入指南。同时提供原生 Gemini (v1beta) 与 OpenAI 兼容 (/v1) 两种接法。
        </p>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <Button
          variant="secondary"
          size="sm"
          @click="router.push('/accounts')"
        >
          <Users class="w-3.5 h-3.5" /> 账号管理
        </Button>
        <Button
          variant="secondary"
          size="sm"
          @click="router.push('/settings')"
        >
          <Sliders class="w-3.5 h-3.5" /> 密钥与诊断导出
        </Button>
      </div>
    </div>

    <!-- Step 0: Network & Address Rule Banner -->
    <div class="bg-white border border-gray-200/80 rounded-2xl p-5 shadow-xs space-y-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-3">
        <div class="flex items-center gap-2">
          <Network class="w-4 h-4 text-brand-600 shrink-0" />
          <h3 class="font-semibold text-gray-900 text-sm">
            第一步：确认服务地址（由客户端后台所在设备决定）
          </h3>
        </div>
        <div class="inline-flex rounded-xl bg-gray-100 p-1 text-xs font-medium self-start sm:self-auto">
          <button
            type="button"
            class="px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
            :class="hostMode === 'local' ? 'bg-white text-gray-900 shadow-xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
            @click="hostMode = 'local'"
          >
            同机运行 (127.0.0.1)
          </button>
          <button
            type="button"
            class="px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
            :class="hostMode === 'lan' ? 'bg-white text-gray-900 shadow-xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
            @click="hostMode = 'lan'"
          >
            跨设备局域网 (LAN IP)
          </button>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs text-gray-600 leading-relaxed">
        <div class="p-3.5 rounded-xl bg-gray-50 border border-gray-200/60 space-y-1.5">
          <div class="font-semibold text-gray-900">
            同机运行（客户端后台与本服务在同一台手机 / 电脑）
          </div>
          <p>
            当酒馆或 Luker 的 Node.js 后台与 <code class="font-mono text-gray-800">aistudio-api</code> 运行在同一部手机 Termux 或同一台电脑时，地址固定填写 <code class="font-mono text-brand-700">127.0.0.1:{{ currentPort }}</code>。
          </p>
        </div>
        <div class="p-3.5 rounded-xl bg-amber-50/60 border border-amber-200/70 space-y-1.5">
          <div class="font-semibold text-amber-900">
            跨设备运行（注意：按客户端后台位置，不按浏览器位置）
          </div>
          <p class="text-amber-800">
            若本服务运行在手机 Termux，而酒馆 / Luker 后台运行在另一台电脑或平板上，必须填写<strong>运行本服务的手机局域网 IP</strong>（如 <code class="font-mono">192.168.1.100:{{ currentPort }}</code>），不能填 <code class="font-mono">127.0.0.1</code>。
          </p>
        </div>
      </div>

      <!-- Quick Copy Bar -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
        <div class="flex items-center justify-between gap-2 px-3.5 py-2.5 rounded-xl bg-gray-900 text-gray-100 text-xs">
          <div class="min-w-0">
            <div class="text-[11px] text-gray-400">
              原生 Gemini 根地址
            </div>
            <div class="font-mono truncate mt-0.5">
              {{ geminiRootUrl }}
            </div>
          </div>
          <button
            type="button"
            class="shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-[11px] transition-colors cursor-pointer"
            @click="handleCopy(geminiRootUrl, 'bar-gemini-url', '原生 Gemini 根地址')"
          >
            <Check
              v-if="copied === 'bar-gemini-url'"
              class="w-3.5 h-3.5 text-emerald-400"
            />
            <Copy
              v-else
              class="w-3.5 h-3.5"
            />
            <span>{{ copied === 'bar-gemini-url' ? '已复制' : '复制' }}</span>
          </button>
        </div>

        <div class="flex items-center justify-between gap-2 px-3.5 py-2.5 rounded-xl bg-gray-900 text-gray-100 text-xs">
          <div class="min-w-0">
            <div class="text-[11px] text-gray-400">
              OpenAI 兼容 Base URL
            </div>
            <div class="font-mono truncate mt-0.5">
              {{ openaiBaseUrl }}
            </div>
          </div>
          <button
            type="button"
            class="shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-[11px] transition-colors cursor-pointer"
            @click="handleCopy(openaiBaseUrl, 'bar-openai-url', 'OpenAI Base URL')"
          >
            <Check
              v-if="copied === 'bar-openai-url'"
              class="w-3.5 h-3.5 text-emerald-400"
            />
            <Copy
              v-else
              class="w-3.5 h-3.5"
            />
            <span>{{ copied === 'bar-openai-url' ? '已复制' : '复制' }}</span>
          </button>
        </div>

        <div class="flex items-center justify-between gap-2 px-3.5 py-2.5 rounded-xl bg-gray-900 text-gray-100 text-xs">
          <div class="min-w-0">
            <div class="text-[11px] text-gray-400">
              服务 API Key 占位示例
            </div>
            <div class="font-mono truncate mt-0.5">
              {{ placeholderKey }}
            </div>
          </div>
          <button
            type="button"
            class="shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-[11px] transition-colors cursor-pointer"
            @click="handleCopy(placeholderKey, 'bar-key', '占位 API Key')"
          >
            <Check
              v-if="copied === 'bar-key'"
              class="w-3.5 h-3.5 text-emerald-400"
            />
            <Copy
              v-else
              class="w-3.5 h-3.5"
            />
            <span>{{ copied === 'bar-key' ? '已复制' : '复制' }}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Step 1: Client & Protocol Switcher + Detailed Steps -->
    <div class="bg-white border border-gray-200/80 rounded-2xl p-5 shadow-xs space-y-5">
      <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4 border-b border-gray-100 pb-4">
        <div class="space-y-1">
          <div class="flex items-center gap-2 flex-wrap">
            <Sparkles class="w-4 h-4 text-brand-600" />
            <h3 class="font-semibold text-gray-900 text-sm">
              第二步：选择客户端与接入协议
            </h3>
            <span class="px-2 py-0.5 rounded-md bg-gray-100 text-gray-600 font-mono text-[11px]">
              {{ clientMeta.badge }}
            </span>
          </div>
          <p class="text-xs text-gray-500">
            {{ clientMeta.summary }}
          </p>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <!-- Client Switcher -->
          <div
            class="inline-flex rounded-xl bg-gray-100 p-1 text-xs font-medium"
            role="tablist"
            aria-label="客户端选择"
          >
            <button
              type="button"
              data-testid="client-tab-sillytavern"
              class="px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
              :class="activeClient === 'sillytavern' ? 'bg-white text-brand-700 shadow-xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
              @click="activeClient = 'sillytavern'"
            >
              酒馆 (SillyTavern)
            </button>
            <button
              type="button"
              data-testid="client-tab-luker"
              class="px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
              :class="activeClient === 'luker' ? 'bg-white text-brand-700 shadow-xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
              @click="activeClient = 'luker'"
            >
              Luker
            </button>
          </div>

          <!-- Protocol Switcher -->
          <div
            class="inline-flex rounded-xl bg-gray-100 p-1 text-xs font-medium"
            role="tablist"
            aria-label="协议选择"
          >
            <button
              type="button"
              data-testid="protocol-tab-gemini"
              class="px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
              :class="activeProtocol === 'gemini' ? 'bg-brand-600 text-white shadow-xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
              @click="activeProtocol = 'gemini'"
            >
              原生 Gemini (推荐)
            </button>
            <button
              type="button"
              data-testid="protocol-tab-openai"
              class="px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
              :class="activeProtocol === 'openai' ? 'bg-brand-600 text-white shadow-xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
              @click="activeProtocol = 'openai'"
            >
              OpenAI 兼容 (/v1)
            </button>
          </div>
        </div>
      </div>

      <!-- Current Path Highlight -->
      <div class="p-4 rounded-xl bg-brand-50/60 border border-brand-200/70 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div class="space-y-1">
          <div class="text-xs font-semibold text-brand-900 flex items-center gap-2">
            <span>当前路径：{{ clientMeta.name }} · {{ protocolMeta.title }}</span>
          </div>
          <p class="text-xs text-brand-800">
            {{ protocolMeta.subtitle }}
          </p>
        </div>
        <div class="shrink-0 text-xs font-mono bg-white px-3 py-2 rounded-lg border border-brand-200 text-brand-900">
          {{ protocolMeta.endpointLabel }}：<strong>{{ protocolMeta.endpointValue }}</strong>
        </div>
      </div>

      <!-- Fields Table / Cards -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div
          v-for="field in connectionFields"
          :key="field.key"
          class="border border-gray-200/80 rounded-xl p-4 flex flex-col justify-between gap-2.5 bg-gray-50/40"
        >
          <div class="space-y-1.5">
            <div class="flex items-center justify-between gap-2">
              <span class="text-xs font-semibold text-gray-900">{{ field.label }}</span>
              <span class="text-[11px] font-mono text-gray-400">{{ field.selector }}</span>
            </div>
            <div class="flex items-center justify-between gap-2 bg-white border border-gray-200/80 rounded-lg px-3 py-2">
              <code class="text-xs font-mono font-semibold text-gray-900 break-all">{{ field.value }}</code>
              <button
                v-if="field.copyable"
                type="button"
                class="shrink-0 flex items-center gap-1 text-[11px] text-gray-600 hover:text-gray-900 px-2 py-0.5 rounded bg-gray-100 hover:bg-gray-200 transition-colors cursor-pointer"
                @click="handleCopy(field.value, `field-${field.key}`, field.label)"
              >
                <Check
                  v-if="copied === `field-${field.key}`"
                  class="w-3 h-3 text-emerald-600"
                />
                <Copy
                  v-else
                  class="w-3 h-3"
                />
                <span>{{ copied === `field-${field.key}` ? '已复制' : '复制' }}</span>
              </button>
            </div>
          </div>
          <p class="text-xs text-gray-500 leading-relaxed">
            {{ field.note }}
          </p>
        </div>
      </div>

      <!-- Minimum Verification Checklist -->
      <div class="p-4 rounded-xl bg-emerald-50/50 border border-emerald-200/70 space-y-2">
        <div class="flex items-center gap-2 text-xs font-semibold text-emerald-900">
          <CheckCircle2 class="w-4 h-4 text-emerald-600 shrink-0" />
          <span>第三步：最小连通与多轮验证建议</span>
        </div>
        <ol class="list-decimal list-inside text-xs text-emerald-900/90 space-y-1 leading-relaxed">
          <li>
            点击客户端的 <strong>Connect / 连接</strong>，确认状态指示变为在线且模型列表可正常加载。
          </li>
          <li>
            先<strong>关闭流式 (Streaming)</strong> 发送一条短消息（例如「请记住暗号 Rosa-1，并用一句话问好」），确认界面完整显示回复且未混入原始 JSON。
          </li>
          <li>
            再<strong>开启流式 (Streaming)</strong> 追问第二轮（例如「刚才约定的暗号是什么？」），确认文字逐段流出、能准确复述上一轮暗号，且发送按钮恢复就绪状态。
          </li>
        </ol>
      </div>
    </div>

    <!-- Parameter Mapping & Boundaries -->
    <div class="bg-white border border-gray-200/80 rounded-2xl p-5 shadow-xs space-y-4">
      <div class="flex items-center gap-2 border-b border-gray-100 pb-3">
        <Wrench class="w-4 h-4 text-brand-600" />
        <h3 class="font-semibold text-gray-900 text-sm">
          OpenAI 兼容接口（/v1/chat/completions）参数转换与内容边界
        </h3>
      </div>

      <p class="text-xs text-gray-500 leading-relaxed">
        本服务区分<strong>「非正文调节参数」</strong>与<strong>「对话正文/媒体/工具结果」</strong>：上游不支持的调节项按规则忽略并记入诊断日志；但凡正文、图片附件或工具历史无法转换，一律明确返回 HTTP 400 错误，绝不静默丢弃内容后假装模型已读。
      </p>

      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse text-xs">
          <thead>
            <tr class="border-b border-gray-200 text-gray-500 bg-gray-50/80">
              <th class="py-2.5 px-3 font-semibold whitespace-nowrap">
                类别
              </th>
              <th class="py-2.5 px-3 font-semibold whitespace-nowrap">
                字段名
              </th>
              <th class="py-2.5 px-3 font-semibold">
                转换与边界行为
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100 text-gray-700">
            <tr
              v-for="row in parameterRows"
              :key="row.group"
              class="hover:bg-gray-50/60"
            >
              <td class="py-2.5 px-3 font-medium text-gray-900 whitespace-nowrap align-top">
                {{ row.group }}
              </td>
              <td class="py-2.5 px-3 font-mono text-[11px] text-brand-700 align-top">
                {{ row.fields }}
              </td>
              <td class="py-2.5 px-3 leading-relaxed align-top">
                {{ row.behavior }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Troubleshooting -->
    <div class="bg-white border border-gray-200/80 rounded-2xl p-5 shadow-xs space-y-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-gray-100 pb-3">
        <div class="flex items-center gap-2">
          <AlertTriangle class="w-4 h-4 text-amber-500" />
          <h3 class="font-semibold text-gray-900 text-sm">
            常见错误排查与诊断包导出
          </h3>
        </div>
        <span class="text-[11px] text-gray-400">
          /v1beta 与 /v1/chat/completions 均纳入同一脱敏诊断采集
        </span>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div
          v-for="(item, idx) in troubleshootingItems"
          :key="idx"
          class="p-4 rounded-xl border border-gray-200/80 bg-gray-50/40 space-y-1.5 text-xs"
        >
          <div class="font-semibold text-gray-900">
            {{ item.symptom }}
          </div>
          <p class="text-gray-600 leading-relaxed">
            <strong>原因：</strong>{{ item.cause }}
          </p>
          <p class="text-brand-800 leading-relaxed">
            <strong>处理：</strong>{{ item.fix }}
          </p>
        </div>
      </div>

      <div class="p-3.5 rounded-xl bg-gray-50 border border-gray-200/70 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-gray-600">
        <div>
          遇到疑难问题时，可在<strong>「系统与模型配置」</strong>页面底部点击<strong>「导出诊断包」</strong>（或在 Termux 执行 <code class="font-mono text-gray-800">aistudio --export-logs</code>），获取包含请求编号、参数兼容决策与完整收发记录的脱敏压缩包。
        </div>
        <Button
          variant="secondary"
          size="sm"
          class="shrink-0"
          @click="router.push('/settings')"
        >
          前往导出诊断包
        </Button>
      </div>
    </div>
  </div>
</template>
