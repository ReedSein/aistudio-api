import { request, requestBlob } from './client.ts'
import type {
  StatsResponse,
  RotationStatusResponse,
  ClearCooldownRequest,
  SystemConfig,
  ApiKeyItem,
  CreateApiKeyRequest,
  UpdateApiKeyRequest,
  UpdateLoggingConfigRequest,
  UpdateLoggingConfigResponse,
  UpdateRuntimeConfigRequest,
  UpdateRuntimeConfigResponse,
  HealthCheckResponse,
  VersionNotice,
  DiagnosticsStatus,
} from '@/types'


export const systemApi = {
  getDiagnostics(): Promise<DiagnosticsStatus> {
    return request<DiagnosticsStatus>('/api/diagnostics')
  },

  exportDiagnostics(): Promise<Blob> {
    return requestBlob('/api/diagnostics/export', {
      method: 'POST',
      headers: { Accept: 'application/gzip' },
    })
  },

  health(): Promise<HealthCheckResponse> {
    return request<HealthCheckResponse>('/health')
  },

  getVersion(): Promise<VersionNotice> {
    return request<VersionNotice>('/version')
  },

  getStats(): Promise<StatsResponse> {
    return request<StatsResponse>('/stats')
  },

  getRotation(): Promise<RotationStatusResponse> {
    return request<RotationStatusResponse>('/rotation')
  },

  clearCooldown(req: ClearCooldownRequest = {}): Promise<{ ok: boolean; accounts: Record<string, unknown> }> {
    return request('/rotation/clear-cooldown', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },
  cleanupGhosts(): Promise<{ ok: boolean; cleaned_count: number; cleaned_ids: string[]; accounts: Record<string, unknown> }> {
    return request('/rotation/cleanup-ghosts', {
      method: 'POST',
    })
  },


  forceNextAccount(): Promise<{ ok: boolean; account: { id: string; name: string; email: string | null } }> {
    return request('/rotation/next', {
      method: 'POST',
    })
  },

  getConfig(): Promise<SystemConfig> {
    return request<SystemConfig>('/config')
  },

  updateConfigYaml(yamlContent: string): Promise<{ ok: boolean; message: string }> {
    return request('/config/yaml', {
      method: 'PUT',
      body: JSON.stringify({ yaml_content: yamlContent }),
    })
  },

  updateLoggingConfig(req: UpdateLoggingConfigRequest): Promise<UpdateLoggingConfigResponse> {
    return request<UpdateLoggingConfigResponse>('/config/logging', {
      method: 'PUT',
      body: JSON.stringify(req),
    })
  },

  updateRuntimeConfig(req: UpdateRuntimeConfigRequest): Promise<UpdateRuntimeConfigResponse> {
    return request<UpdateRuntimeConfigResponse>('/config/runtime', {
      method: 'PUT',
      body: JSON.stringify(req),
    })
  },

  listApiKeys(): Promise<ApiKeyItem[]> {
    return request<ApiKeyItem[]>('/api-keys')
  },

  createApiKey(req: CreateApiKeyRequest): Promise<ApiKeyItem> {
    return request<ApiKeyItem>('/api-keys', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  deleteApiKey(keyValue: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/api-keys/${encodeURIComponent(keyValue)}`, {
      method: 'DELETE',
    })
  },

  updateApiKeyName(keyValue: string, req: UpdateApiKeyRequest): Promise<ApiKeyItem> {
    return request<ApiKeyItem>(`/api-keys/${encodeURIComponent(keyValue)}`, {
      method: 'PUT',
      body: JSON.stringify(req),
    })
  },
}
