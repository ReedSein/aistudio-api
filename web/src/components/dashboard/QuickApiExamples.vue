<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Code2, Terminal, Copy, Check, BookOpen } from 'lucide-vue-next'
import { useClipboard } from '@/composables/useClipboard.ts'

type ExampleProtocol = 'gemini' | 'openai'

const router = useRouter()
const { copied, copy } = useClipboard()
const activeProtocol = ref<ExampleProtocol>('gemini')

const geminiSnippets = [
  {
    key: 'gemini-curl',
    title: 'cURL (原生 Gemini /v1beta)',
    lang: 'bash',
    code: `curl http://localhost:11451/v1beta/models/gemini-3.8-flash:generateContent \\
  -H "x-goog-api-key: your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'`,
  },
  {
    key: 'gemini-sdk',
    title: 'Python SDK (google-genai)',
    lang: 'python',
    code: `from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:11451",
    },
)

response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="Hello"
)
print(response.text)`,
  },
]

const openaiSnippets = [
  {
    key: 'openai-curl',
    title: 'cURL (OpenAI 兼容 /v1/chat/completions)',
    lang: 'bash',
    code: `curl http://localhost:11451/v1/chat/completions \\
  -H "Authorization: Bearer your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gemini-3.8-flash",
    "messages": [{"role": "user", "content": "Hello"}]
  }'`,
  },
  {
    key: 'openai-sdk',
    title: 'Python SDK (openai)',
    lang: 'python',
    code: `from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",
    base_url="http://localhost:11451/v1",
)

response = client.chat.completions.create(
    model="gemini-3.8-flash",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)`,
  },
]

const codeSnippets = computed(() =>
  activeProtocol.value === 'gemini' ? geminiSnippets : openaiSnippets,
)

function handleCopy(code: string, key: string) {
  void copy(code, key, '已复制调用示例到剪贴板')
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-6 shadow-xs space-y-4">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-3">
      <div class="flex items-center gap-2">
        <Code2 class="w-4 h-4 text-brand-600" />
        <h3 class="font-semibold text-gray-900 text-sm">
          快速调用示例
        </h3>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <div
          class="inline-flex rounded-xl bg-gray-100 p-1 text-xs font-medium"
          role="tablist"
          aria-label="调用示例协议切换"
        >
          <button
            type="button"
            data-testid="quick-example-gemini"
            class="px-2.5 py-1 rounded-lg transition-colors cursor-pointer"
            :class="activeProtocol === 'gemini' ? 'bg-white text-brand-700 shadow-xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
            @click="activeProtocol = 'gemini'"
          >
            原生 Gemini (/v1beta)
          </button>
          <button
            type="button"
            data-testid="quick-example-openai"
            class="px-2.5 py-1 rounded-lg transition-colors cursor-pointer"
            :class="activeProtocol === 'openai' ? 'bg-white text-brand-700 shadow-xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
            @click="activeProtocol = 'openai'"
          >
            OpenAI 兼容 (/v1)
          </button>
        </div>

        <button
          type="button"
          class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium text-brand-700 bg-brand-50 hover:bg-brand-100 transition-colors cursor-pointer"
          @click="router.push('/guides')"
        >
          <BookOpen class="w-3.5 h-3.5" />
          <span>酒馆 / Luker 接入教程</span>
        </button>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div
        v-for="snippet in codeSnippets"
        :key="snippet.key"
        class="bg-gray-900 text-gray-100 rounded-xl p-4 flex flex-col justify-between overflow-hidden shadow-xs"
      >
        <div class="flex items-center justify-between pb-2 border-b border-gray-800 text-xs font-medium text-gray-400">
          <div class="flex items-center gap-1.5">
            <Terminal class="w-3.5 h-3.5 text-brand-400" />
            <span>{{ snippet.title }}</span>
          </div>
          <button
            type="button"
            class="flex items-center gap-1 text-[11px] text-gray-400 hover:text-white px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 transition-colors cursor-pointer"
            @click="handleCopy(snippet.code, snippet.key)"
          >
            <Check
              v-if="copied === snippet.key"
              class="w-3 h-3 text-emerald-400"
            />
            <Copy
              v-else
              class="w-3 h-3"
            />
            <span>{{ copied === snippet.key ? '已复制' : '复制' }}</span>
          </button>
        </div>

        <pre class="mt-3 text-xs font-mono overflow-x-auto text-gray-200 leading-relaxed"><code>{{ snippet.code }}</code></pre>
      </div>
    </div>
  </div>
</template>
