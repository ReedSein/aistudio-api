export interface SystemConfig {
  port: number
  browser_port: number
  browser_headless: boolean
  proxy_configured: boolean
  auth_enabled: boolean
  timeout_stream: number
  timeout_replay: number
  browser_idle_timeout: number
  env_overrides?: string[]
  yaml_content: string
  log_level?: string
  dump_requests?: boolean
  debug_env_active?: boolean
  dump_to_file?: boolean
  dump_dir?: string
}
export interface UpdateLoggingConfigRequest {
  level?: string
  dump_requests?: boolean
  dump_to_file?: boolean
  dump_dir?: string
}
export interface UpdateLoggingConfigResponse {
  ok: boolean
  log_level: string
  dump_requests: boolean
  debug_env_active: boolean
  dump_to_file: boolean
  dump_dir: string
}

export interface UpdateRuntimeConfigRequest {
  timeout_stream?: number | null
  timeout_replay?: number | null
  browser_idle_timeout?: number | null
}

export interface UpdateRuntimeConfigResponse {
  ok: boolean
  timeout_stream: number
  timeout_replay: number
  browser_idle_timeout: number
  env_overrides: string[]
}

export interface HealthCheckResponse {
  status: string
  busy: boolean
}
export interface ApiKeyItem {
  name: string
  key: string
  created_at?: string | null
}

export interface CreateApiKeyRequest {
  name?: string
  key?: string
}

export interface UpdateApiKeyRequest {
  name: string
}

export interface VersionNotice {
  channel: 'development' | 'stable'
  state: 'newer' | 'current' | 'unpublished' | 'unreachable' | 'unknown' | null
  current: string | null
  latest: string | null
  release_url: string | null
}
