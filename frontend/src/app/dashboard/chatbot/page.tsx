'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { chatbot, bulkMessage } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Bot, Save, Loader2, RefreshCw, Plus, Trash2, Pencil, X,
  Zap, MousePointerClick, MessageSquare, Search, ChevronDown, Ban,
} from 'lucide-react'


// ── Searchable Template Selector (top-level to avoid remount issues) ──

interface SearchableTemplateSelectorProps {
  value: string
  onChange: (v: string) => void
  search: string
  setSearch: (s: string) => void
  open: boolean
  setOpen: (o: boolean) => void
  containerRef: React.RefObject<HTMLDivElement>
  templates: WATemplate[]
  templatesLoading: boolean
  placeholder?: string
}

function SearchableTemplateSelector({
  value,
  onChange,
  search,
  setSearch,
  open,
  setOpen,
  containerRef,
  templates,
  templatesLoading,
  placeholder = 'Select a template',
}: SearchableTemplateSelectorProps) {
  const filtered = templates.filter(
    (t) =>
      t.name.toLowerCase().includes(search.toLowerCase()) ||
      t.language.toLowerCase().includes(search.toLowerCase())
  )
  const currentLabel = value
    ? templates.find((t) => t.name === value)
      ? `${value} (${templates.find((t) => t.name === value)?.language})`
      : value
    : null

  return (
    <div className="relative" ref={containerRef}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => {
          setOpen(!open)
          if (!open) setSearch('')
        }}
        className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className={currentLabel ? 'text-foreground' : 'text-muted-foreground'}>
          {templatesLoading ? 'Loading templates…' : currentLabel ?? placeholder}
        </span>
        <ChevronDown className={`h-4 w-4 opacity-50 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {/* Dropdown */}
      {open && (
        <div
          className="absolute z-50 mt-1 w-full rounded-md border bg-popover text-popover-foreground shadow-lg"
          style={{ maxHeight: '280px', display: 'flex', flexDirection: 'column' }}
        >
          {/* Search input */}
          <div className="flex items-center border-b px-3 py-2 gap-2">
            <Search className="h-4 w-4 text-muted-foreground shrink-0" />
            <input
              autoFocus
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              placeholder="Search template…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search && (
              <button onClick={() => setSearch('')} className="text-muted-foreground hover:text-foreground">
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* List */}
          <div className="overflow-y-auto flex-1 py-1">
            {templatesLoading && (
              <p className="px-3 py-2 text-sm text-muted-foreground">Loading…</p>
            )}
            {!templatesLoading && filtered.length === 0 && (
              <p className="px-3 py-2 text-sm text-muted-foreground">
                {templates.length === 0 ? 'No approved templates found' : 'No results'}
              </p>
            )}
            {filtered.map((t) => (
              <button
                key={`${t.name}-${t.language}`}
                type="button"
                className={`flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground ${value === t.name ? 'bg-accent/50 font-medium' : ''
                  }`}
                onClick={() => {
                  onChange(t.name)
                  setOpen(false)
                  setSearch('')
                }}
              >
                <span className="inline-flex items-center justify-center w-4 h-4 rounded bg-green-100 text-green-700 text-[10px] font-bold">T</span>
                <span>{t.name}</span>
                <span className="ml-auto text-xs text-muted-foreground">{t.language}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}


// ── Types ─────────────────────────────────────────────────────────

interface ChatbotSettings {
  is_enabled: number | boolean
  fallback_message: string
  fallback_template_name?: string
  fallback_cooldown_hours?: number
  use_ai?: boolean
}

interface ChatbotRule {
  id: number
  keyword: string
  response: string
  response_type: string
  match_type: string
  priority: number
  is_active: number | boolean
  _doc_id?: string
}

interface ButtonMapping {
  id: number
  button_text: string
  template_name: string
  is_active: boolean
  priority: number
}

interface WATemplate {
  name: string
  language: string
  status: string
}


export default function ChatbotPage() {
  const { toast } = useToast()

  // ── State ───────────────────────────────────────────────────────
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const [settings, setSettings] = useState<ChatbotSettings>({
    is_enabled: false,
    fallback_message: '',
    fallback_template_name: '',
    fallback_cooldown_hours: 24,
  })

  // Rules
  const [rules, setRules] = useState<ChatbotRule[]>([])
  const [showRuleForm, setShowRuleForm] = useState(false)
  const [editingRule, setEditingRule] = useState<ChatbotRule | null>(null)
  const [ruleForm, setRuleForm] = useState({
    keyword: '', response: '', response_type: 'text', match_type: 'contains', priority: 0,
  })
  const [savingRule, setSavingRule] = useState(false)

  // Button Mappings
  const [mappings, setMappings] = useState<ButtonMapping[]>([])
  const [showMappingForm, setShowMappingForm] = useState(false)
  const [editingMapping, setEditingMapping] = useState<ButtonMapping | null>(null)
  const [mappingForm, setMappingForm] = useState({
    button_text: '', template_name: '', priority: 0,
  })
  const [savingMapping, setSavingMapping] = useState(false)

  // Templates (for selectors)
  const [templates, setTemplates] = useState<WATemplate[]>([])
  const [templatesLoading, setTemplatesLoading] = useState(false)

  // Fallback template searchable dropdown state
  const [fallbackSearch, setFallbackSearch] = useState('')
  const [fallbackOpen, setFallbackOpen] = useState(false)
  const fallbackRef = useRef<HTMLDivElement>(null)

  // Rule template searchable dropdown state
  const [ruleTemplateSearch, setRuleTemplateSearch] = useState('')
  const [ruleTemplateOpen, setRuleTemplateOpen] = useState(false)
  const ruleTemplateRef = useRef<HTMLDivElement>(null)

  // Mapping template searchable dropdown state
  const [mappingTemplateSearch, setMappingTemplateSearch] = useState('')
  const [mappingTemplateOpen, setMappingTemplateOpen] = useState(false)
  const mappingTemplateRef = useRef<HTMLDivElement>(null)

  // ── Data Fetching ─────────────────────────────────────────────

  const fetchData = useCallback(async () => {
    try {
      // Use allSettled so one failing call doesn't block the others from updating
      const [settingsResult, rulesResult, mappingsResult] = await Promise.allSettled([
        chatbot.getSettings(),
        chatbot.getRules(),
        chatbot.getButtonMappings(),
      ])
      if (settingsResult.status === 'fulfilled') {
        setSettings(settingsResult.value.data.settings)
      } else {
        console.error('Failed to fetch chatbot settings:', settingsResult.reason)
      }
      if (rulesResult.status === 'fulfilled') {
        setRules(rulesResult.value.data.rules || [])
      } else {
        console.error('Failed to fetch chatbot rules:', rulesResult.reason)
      }
      if (mappingsResult.status === 'fulfilled') {
        setMappings(mappingsResult.value.data.mappings || [])
      } else {
        console.error('Failed to fetch button mappings:', mappingsResult.reason)
      }
    } catch (error) {
      console.error('Failed to fetch chatbot data:', error)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchTemplates = useCallback(async () => {
    if (templates.length > 0) return // only fetch once
    setTemplatesLoading(true)
    try {
      const res = await bulkMessage.templates()
      const tpls: WATemplate[] = (res.data?.templates || [])
        .filter((t: any) => t.status === 'APPROVED')
        .map((t: any) => ({
          name: t.name,
          language: t.language,
          status: t.status,
        }))
      setTemplates(tpls)
    } catch {
      // templates endpoint may fail if WA not configured — that's ok
    } finally {
      setTemplatesLoading(false)
    }
  }, [templates.length])

  useEffect(() => {
    fetchData()
    fetchTemplates()
  }, [fetchData, fetchTemplates])

  const handleRefresh = async () => {
    setRefreshing(true)
    try { await fetchData() } finally { setRefreshing(false) }
  }

  // ── Settings Actions ──────────────────────────────────────────

  const handleToggle = async (enabled: boolean) => {
    try {
      await chatbot.updateSettings({
        is_enabled: enabled,
        fallback_message: settings.fallback_message,
        fallback_template_name: settings.fallback_template_name,
        fallback_cooldown_hours: settings.fallback_cooldown_hours,
      })
      setSettings({ ...settings, is_enabled: enabled ? 1 : 0 })
      toast({ title: enabled ? 'Chatbot enabled' : 'Chatbot disabled' })
    } catch {
      toast({ title: 'Error', description: 'Failed to update settings', variant: 'destructive' })
    }
  }

  const handleSaveSettings = async () => {
    setSaving(true)
    try {
      await chatbot.updateSettings({
        is_enabled: settings.is_enabled === 1 || settings.is_enabled === true,
        fallback_message: settings.fallback_message,
        fallback_template_name: settings.fallback_template_name,
        fallback_cooldown_hours: settings.fallback_cooldown_hours,
      })
      toast({ title: 'Settings saved' })
    } catch {
      toast({ title: 'Error', description: 'Failed to save settings', variant: 'destructive' })
    } finally {
      setSaving(false)
    }
  }

  // ── Rule Actions ──────────────────────────────────────────────

  const openRuleForm = (rule?: ChatbotRule) => {
    if (rule) {
      setEditingRule(rule)
      setRuleForm({
        keyword: rule.keyword,
        response: rule.response,
        response_type: rule.response_type || 'text',
        match_type: rule.match_type || 'contains',
        priority: rule.priority || 0,
      })
    } else {
      setEditingRule(null)
      setRuleForm({ keyword: '', response: '', response_type: 'text', match_type: 'contains', priority: 0 })
    }
    setShowRuleForm(true)
  }

  const handleSaveRule = async () => {
    if (!ruleForm.keyword.trim() || !ruleForm.response.trim()) {
      toast({ title: 'Error', description: 'Keyword and response are required', variant: 'destructive' })
      return
    }
    setSavingRule(true)
    try {
      if (editingRule) {
        await chatbot.updateRule(editingRule.id, ruleForm)
        toast({ title: 'Rule updated' })
      } else {
        await chatbot.createRule(ruleForm)
        toast({ title: 'Rule created' })
      }
      setShowRuleForm(false)
      setEditingRule(null)
      await fetchData()
    } catch {
      toast({ title: 'Error', description: 'Failed to save rule', variant: 'destructive' })
    } finally {
      setSavingRule(false)
    }
  }

  const handleDeleteRule = async (id: number) => {
    try {
      await chatbot.deleteRule(id)
      toast({ title: 'Rule deleted' })
      await fetchData()
    } catch {
      toast({ title: 'Error', description: 'Failed to delete rule', variant: 'destructive' })
    }
  }

  const handleToggleRule = async (rule: ChatbotRule) => {
    const newActive = !(rule.is_active === 1 || rule.is_active === true)
    try {
      await chatbot.updateRule(rule.id, {
        keyword: rule.keyword,
        response: rule.response,
        response_type: rule.response_type,
        match_type: rule.match_type,
        is_active: newActive,
        priority: rule.priority,
      })
      await fetchData()
    } catch {
      toast({ title: 'Error', description: 'Failed to toggle rule', variant: 'destructive' })
    }
  }

  // ── Button Mapping Actions ────────────────────────────────────

  const openMappingForm = (mapping?: ButtonMapping) => {
    if (mapping) {
      setEditingMapping(mapping)
      setMappingForm({
        button_text: mapping.button_text,
        template_name: mapping.template_name,
        priority: mapping.priority || 0,
      })
    } else {
      setEditingMapping(null)
      setMappingForm({ button_text: '', template_name: '', priority: 0 })
    }
    setShowMappingForm(true)
  }

  const handleSaveMapping = async () => {
    if (!mappingForm.button_text.trim()) {
      toast({ title: 'Error', description: 'Button Text is required', variant: 'destructive' })
      return
    }
    if (!mappingForm.template_name) {
      toast({ title: 'Error', description: 'Template is required', variant: 'destructive' })
      return
    }
    setSavingMapping(true)
    try {
      if (editingMapping) {
        await chatbot.updateButtonMapping(editingMapping.id, mappingForm)
        toast({ title: 'Mapping updated' })
      } else {
        await chatbot.createButtonMapping(mappingForm)
        toast({ title: 'Mapping created' })
      }
      setShowMappingForm(false)
      setEditingMapping(null)
      await fetchData()
    } catch {
      toast({ title: 'Error', description: 'Failed to save mapping', variant: 'destructive' })
    } finally {
      setSavingMapping(false)
    }
  }

  const handleDeleteMapping = async (id: number) => {
    try {
      await chatbot.deleteButtonMapping(id)
      toast({ title: 'Mapping deleted' })
      await fetchData()
    } catch {
      toast({ title: 'Error', description: 'Failed to delete mapping', variant: 'destructive' })
    }
  }

  // ── Template Selector Component (plain, used in forms) ──────────

  const TemplateSelector = ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger>
        <SelectValue placeholder={templatesLoading ? 'Loading templates...' : 'Select a template'} />
      </SelectTrigger>
      <SelectContent>
        {templates.length === 0 && !templatesLoading && (
          <SelectItem value="_none" disabled>No approved templates found</SelectItem>
        )}
        {templates.map((t) => (
          <SelectItem key={`${t.name}-${t.language}`} value={t.name}>
            {t.name} ({t.language})
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )

  // ── Fallback Template Searchable Selector ────────────────────────

  const filteredFallbackTemplates = templates.filter(
    (t) =>
      t.name.toLowerCase().includes(fallbackSearch.toLowerCase()) ||
      t.language.toLowerCase().includes(fallbackSearch.toLowerCase())
  )

  const FallbackTemplateSelector = () => {
    const currentLabel = settings.fallback_template_name
      ? templates.find((t) => t.name === settings.fallback_template_name)
        ? `${settings.fallback_template_name} (${templates.find((t) => t.name === settings.fallback_template_name)?.language})`
        : settings.fallback_template_name
      : null

    return (
      <div className="relative" ref={fallbackRef}>
        {/* Trigger */}
        <button
          type="button"
          onClick={() => {
            setFallbackOpen((o) => !o)
            if (!fallbackOpen) setFallbackSearch('')
          }}
          className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span className={currentLabel ? 'text-foreground' : 'text-muted-foreground'}>
            {templatesLoading
              ? 'Loading templates…'
              : currentLabel ?? 'Select a template (or None)'}
          </span>
          <ChevronDown className={`h-4 w-4 opacity-50 transition-transform ${fallbackOpen ? 'rotate-180' : ''}`} />
        </button>

        {/* Dropdown */}
        {fallbackOpen && (
          <div
            className="absolute z-50 mt-1 w-full rounded-md border bg-popover text-popover-foreground shadow-lg"
            style={{ maxHeight: '280px', display: 'flex', flexDirection: 'column' }}
          >
            {/* Search input */}
            <div className="flex items-center border-b px-3 py-2 gap-2">
              <Search className="h-4 w-4 text-muted-foreground shrink-0" />
              <input
                autoFocus
                className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                placeholder="Search template…"
                value={fallbackSearch}
                onChange={(e) => setFallbackSearch(e.target.value)}
              />
              {fallbackSearch && (
                <button onClick={() => setFallbackSearch('')} className="text-muted-foreground hover:text-foreground">
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            {/* List */}
            <div className="overflow-y-auto flex-1 py-1">
              {/* None option */}
              <button
                type="button"
                className={`flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground ${!settings.fallback_template_name ? 'bg-accent/50 font-medium' : ''
                  }`}
                onClick={() => {
                  setSettings({ ...settings, fallback_template_name: '' })
                  setFallbackOpen(false)
                  setFallbackSearch('')
                }}
              >
                <Ban className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-muted-foreground italic">None – no fallback template</span>
              </button>

              {/* Separator */}
              <div className="mx-2 my-1 border-t" />

              {/* Template items */}
              {templatesLoading && (
                <p className="px-3 py-2 text-sm text-muted-foreground">Loading…</p>
              )}
              {!templatesLoading && filteredFallbackTemplates.length === 0 && (
                <p className="px-3 py-2 text-sm text-muted-foreground">
                  {templates.length === 0 ? 'No approved templates found' : 'No results'}
                </p>
              )}
              {filteredFallbackTemplates.map((t) => (
                <button
                  key={`${t.name}-${t.language}`}
                  type="button"
                  className={`flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground ${settings.fallback_template_name === t.name ? 'bg-accent/50 font-medium' : ''
                    }`}
                  onClick={() => {
                    setSettings({ ...settings, fallback_template_name: t.name })
                    setFallbackOpen(false)
                    setFallbackSearch('')
                  }}
                >
                  <span className="inline-flex items-center justify-center w-4 h-4 rounded bg-green-100 text-green-700 text-[10px] font-bold">T</span>
                  <span>{t.name}</span>
                  <span className="ml-auto text-xs text-muted-foreground">{t.language}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  // Close fallback dropdown when clicking outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (fallbackRef.current && !fallbackRef.current.contains(e.target as Node)) {
        setFallbackOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Close rule template dropdown when clicking outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ruleTemplateRef.current && !ruleTemplateRef.current.contains(e.target as Node)) {
        setRuleTemplateOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Close mapping template dropdown when clicking outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (mappingTemplateRef.current && !mappingTemplateRef.current.contains(e.target as Node)) {
        setMappingTemplateOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // ── Loading Skeleton ──────────────────────────────────────────

  if (loading) {
    return (
      <div className="space-y-6">
        <div>
          <Skeleton className="h-7 w-64" />
          <Skeleton className="h-4 w-80 mt-2" />
        </div>
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <Skeleton className="h-5 w-36" />
              <Skeleton className="h-4 w-48 mt-1" />
            </CardHeader>
            <CardContent className="space-y-4">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <Skeleton className="h-5 w-36" />
              <Skeleton className="h-4 w-48 mt-1" />
            </CardHeader>
            <CardContent className="space-y-4">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  const isEnabled = settings.is_enabled === 1 || settings.is_enabled === true

  // ── Render ────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">WhatsApp Chatbot</h1>
          <p className="text-gray-500 mt-1">Configure auto-reply rules, button mappings, and fallback triggers</p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={refreshing}
            aria-label="Refresh"
            title="Refresh"
            className="h-9 w-9 p-0"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleSaveSettings}
            disabled={saving}
            aria-label="Save Settings"
            title="Save Settings"
            className="h-9 w-9 p-0"
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        {/* ── Settings Card ─────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Bot className="h-5 w-5" />
                  Chatbot Settings
                </CardTitle>
                <CardDescription>Configure auto-reply behavior and fallback trigger</CardDescription>
              </div>
              <Switch checked={isEnabled} onCheckedChange={handleToggle} />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center space-x-2 p-3 rounded-lg bg-gray-50">
              <Bot className={`h-5 w-5 ${isEnabled ? 'text-green-500' : 'text-gray-400'}`} />
              <span className={isEnabled ? 'text-green-700 font-medium' : 'text-gray-500'}>
                {isEnabled ? 'Chatbot is active' : 'Chatbot is disabled'}
              </span>
            </div>

            {/* Fallback Template */}
            <div className="space-y-2">
              <Label htmlFor="fallbackTemplate">Fallback Template</Label>
              <FallbackTemplateSelector />
              <p className="text-xs text-gray-500">
                Sent when no button or keyword rule matches (rate-limited per contact)
              </p>
            </div>

            {/* Cooldown Hours */}
            <div className="space-y-2">
              <Label htmlFor="cooldownHours">Fallback Cooldown (hours)</Label>
              <Input
                id="cooldownHours"
                type="number"
                min={1}
                max={720}
                value={settings.fallback_cooldown_hours || 24}
                onChange={(e) => setSettings({
                  ...settings,
                  fallback_cooldown_hours: parseInt(e.target.value) || 24,
                })}
              />
              <p className="text-xs text-gray-500">
                How many hours to wait before sending the fallback template again to the same contact
              </p>
            </div>


          </CardContent>
        </Card>

        {/* ── Button Mappings Card ──────────────────────────────── */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <MousePointerClick className="h-5 w-5" />
                  Button Mappings
                </CardTitle>
                <CardDescription>Map WhatsApp button clicks to template responses</CardDescription>
              </div>
              <Button size="sm" onClick={() => openMappingForm()}>
                <Plus className="h-4 w-4 mr-1" />
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {mappings.length === 0 && !showMappingForm && (
              <div className="text-center py-8 text-gray-400">
                <MousePointerClick className="h-10 w-10 mx-auto mb-2 opacity-40" />
                <p>No button mappings configured</p>
                <p className="text-xs mt-1">Click &quot;Add&quot; to map a button to a template</p>
              </div>
            )}

            {/* Mappings Table */}
            {mappings.length > 0 && (
              <div className="space-y-2 mb-4">
                {mappings.map((m) => (
                  <div
                    key={m.id}
                    className={`flex items-center justify-between p-3 rounded-lg border ${m.is_active ? 'bg-white border-gray-200' : 'bg-gray-50 border-gray-100 opacity-60'
                      }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        {m.button_text && (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-50 text-blue-700">
                            Text: {m.button_text}
                          </span>
                        )}

                        <span className="text-gray-400">→</span>
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-50 text-green-700">
                          📋 {m.template_name}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 ml-2">
                      <Button variant="ghost" size="sm" onClick={() => openMappingForm(m)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => handleDeleteMapping(m.id)}>
                        <Trash2 className="h-3.5 w-3.5 text-red-500" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Mapping Form */}
            {showMappingForm && (
              <div className="border rounded-lg p-4 space-y-3 bg-gray-50">
                <div className="flex items-center justify-between">
                  <h4 className="font-medium text-sm">
                    {editingMapping ? 'Edit Mapping' : 'New Button Mapping'}
                  </h4>
                  <Button variant="ghost" size="sm" onClick={() => { setShowMappingForm(false); setEditingMapping(null) }}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Button Text</Label>
                  <Input
                    placeholder="e.g. Morning"
                    value={mappingForm.button_text}
                    onChange={(e) => setMappingForm({ ...mappingForm, button_text: e.target.value })}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Template</Label>
                  <SearchableTemplateSelector
                    value={mappingForm.template_name}
                    onChange={(v) => setMappingForm({ ...mappingForm, template_name: v })}
                    search={mappingTemplateSearch}
                    setSearch={setMappingTemplateSearch}
                    open={mappingTemplateOpen}
                    setOpen={setMappingTemplateOpen}
                    containerRef={mappingTemplateRef}
                    templates={templates}
                    templatesLoading={templatesLoading}
                    placeholder="Select a template"
                  />
                </div>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" size="sm" onClick={() => { setShowMappingForm(false); setEditingMapping(null) }}>
                    Cancel
                  </Button>
                  <Button size="sm" onClick={handleSaveMapping} disabled={savingMapping}>
                    {savingMapping && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                    {editingMapping ? 'Update' : 'Create'}
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ── Keyword Rules Card (full width) ──────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Zap className="h-5 w-5" />
                Keyword Automation Rules
              </CardTitle>
              <CardDescription>
                Auto-reply with text messages or templates when keywords are detected
              </CardDescription>
            </div>
            <Button size="sm" onClick={() => openRuleForm()}>
              <Plus className="h-4 w-4 mr-1" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {rules.length === 0 && !showRuleForm && (
            <div className="text-center py-8 text-gray-400">
              <MessageSquare className="h-10 w-10 mx-auto mb-2 opacity-40" />
              <p>No keyword rules configured</p>
              <p className="text-xs mt-1">Click &quot;Add Rule&quot; to create an auto-reply rule</p>
            </div>
          )}

          {/* Rules List */}
          {rules.length > 0 && (
            <div className="space-y-2 mb-4">
              {rules.map((r) => {
                const active = r.is_active === 1 || r.is_active === true
                return (
                  <div
                    key={r.id}
                    className={`flex items-center justify-between p-3 rounded-lg border ${active ? 'bg-white border-gray-200' : 'bg-gray-50 border-gray-100 opacity-60'
                      }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-50 text-amber-700">
                          {r.match_type || 'contains'}
                        </span>
                        <span className="font-mono text-sm font-medium text-gray-800">
                          &quot;{r.keyword}&quot;
                        </span>
                        <span className="text-gray-400">→</span>
                        {r.response_type === 'template' ? (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-50 text-green-700">
                            📋 {r.response}
                          </span>
                        ) : (
                          <span className="text-sm text-gray-600 truncate max-w-[200px]">
                            💬 {r.response}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 ml-2">
                      <Switch
                        checked={active}
                        onCheckedChange={() => handleToggleRule(r)}
                      />
                      <Button variant="ghost" size="sm" onClick={() => openRuleForm(r)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => handleDeleteRule(r.id)}>
                        <Trash2 className="h-3.5 w-3.5 text-red-500" />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Rule Form */}
          {showRuleForm && (
            <div className="border rounded-lg p-4 space-y-3 bg-gray-50">
              <div className="flex items-center justify-between">
                <h4 className="font-medium text-sm">
                  {editingRule ? 'Edit Rule' : 'New Keyword Rule'}
                </h4>
                <Button variant="ghost" size="sm" onClick={() => { setShowRuleForm(false); setEditingRule(null) }}>
                  <X className="h-4 w-4" />
                </Button>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label className="text-xs">Keyword</Label>
                  <Input
                    placeholder="e.g. pricing"
                    value={ruleForm.keyword}
                    onChange={(e) => setRuleForm({ ...ruleForm, keyword: e.target.value })}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Match Type</Label>
                  <Select value={ruleForm.match_type} onValueChange={(v) => setRuleForm({ ...ruleForm, match_type: v })}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="contains">Contains</SelectItem>
                      <SelectItem value="exact">Exact Match</SelectItem>
                      <SelectItem value="starts_with">Starts With</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-1">
                <Label className="text-xs">Response Type</Label>
                <Select value={ruleForm.response_type} onValueChange={(v) => setRuleForm({ ...ruleForm, response_type: v, response: '' })}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="text">💬 Text Message</SelectItem>
                    <SelectItem value="template">📋 Template</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1">
                <Label className="text-xs">
                  {ruleForm.response_type === 'template' ? 'Template' : 'Response Text'}
                </Label>
                {ruleForm.response_type === 'template' ? (
                  <SearchableTemplateSelector
                    value={ruleForm.response}
                    onChange={(v) => setRuleForm({ ...ruleForm, response: v })}
                    search={ruleTemplateSearch}
                    setSearch={setRuleTemplateSearch}
                    open={ruleTemplateOpen}
                    setOpen={setRuleTemplateOpen}
                    containerRef={ruleTemplateRef}
                    templates={templates}
                    templatesLoading={templatesLoading}
                    placeholder="Select a template"
                  />
                ) : (
                  <Textarea
                    placeholder="Type your auto-reply message..."
                    value={ruleForm.response}
                    onChange={(e) => setRuleForm({ ...ruleForm, response: e.target.value })}
                    rows={3}
                  />
                )}
              </div>

              <div className="space-y-1">
                <Label className="text-xs">Priority (higher = checked first)</Label>
                <Input
                  type="number"
                  min={0}
                  value={ruleForm.priority}
                  onChange={(e) => setRuleForm({ ...ruleForm, priority: parseInt(e.target.value) || 0 })}
                />
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => { setShowRuleForm(false); setEditingRule(null) }}>
                  Cancel
                </Button>
                <Button size="sm" onClick={handleSaveRule} disabled={savingRule}>
                  {savingRule && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                  {editingRule ? 'Update Rule' : 'Create Rule'}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
