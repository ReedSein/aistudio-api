import type { AccountRotationStats } from './rotation.ts'

export interface Account {
  id: string
  name: string
  email: string | null
  created_at: string
  last_used?: string | null
  auth_user: string
  cookie_id?: string | null
  tier?: 'Free' | 'Plus' | 'Pro' | 'Ultra' | null
  tier_checked_at?: string | null
  tier_last_error?: string | null
}

export interface AccountWithStats extends Omit<Account, 'last_used'>, Partial<AccountRotationStats> {
  last_used?: string | null
}

export interface ImportCookiesRequest {
  cookies: string
  name?: string
  email?: string
  account_id?: string
  auth_user?: string
}

export interface ImportCookiesResponse {
  account_id: string
  name: string
  cookie_count: number
  domain_summary: Record<string, number>
  auth_user: string
}

export interface ProbeAndImportRequest {
  cookies: string
  name_prefix?: string
}

export interface ProbeAndImportResponse {
  imported_count: number
  accounts: Account[]
}

export interface BundleAccountItem {
  id?: string
  name?: string
  email?: string
  cookies: string
  auth_user?: string
}

export interface ImportBundleRequest {
  file_path?: string
  content?: string
  accounts?: BundleAccountItem[]
}

export interface ImportBundleResponse {
  imported_count: number
  accounts: Account[]
}
