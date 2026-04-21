'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { chatbot, bulkMessage } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { Skeleton } from '@/components/ui/skeleton'
import { Bot, Save, Loader2, RefreshCw, Plus, Trash2, Pencil, X, Zap, MousePointerClick, MessageSquare, Search, ChevronDown, Ban } from 'lucide-react'

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
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [settings, setSettings] = useState<ChatbotSettings>({
    is_enabled: false,
    fallback_message: '',
    fallback_template_name: '',
    fallback_cooldown_hours: 24,
  })
  const [rules, setRules] = useState<ChatbotRule[]>([])
  const [showRuleForm, setShowRuleForm] = useState(false)
  const [editingRule, setEditingRule] = useState<ChatbotRule | null>(null)
  const [ruleForm, setRuleForm] = useState({
    keyword: '', response: '', response_type: 'text', match_type: 'contains', priority: 0,
  })
  const [savingRule, setSavingRule] = useState(false)
  const [mappings, setMappings] = useState<ButtonMapping[]>([])
  const [showMappingForm, setShowMappingForm] = useState(false)
  const [editingMapping, setEditingMapping] = useState<ButtonMapping | null>(null)
  const [mappingForm, setMappingForm] = useState({
    button_text: '', template_name: '', priority: 0,
  })
  const [savingMapping, setSavingMapping] = useState(false)
  const [templates, setTemplates] = useState<WATemplate[]>([])
  const [templatesLoading, setTemplatesLoading] = useState(false)
  const [fallbackSearch, setFallbackSearch] = useState('')
  const [fallbackOpen, setFallbackOpen] = useState(false)
  const fallbackRef = useRef<HTMLDivElement>(null)

  const fetchData = useCallback(async () => {
    try {
      const [settingsResult, rulesResult, mappingsResult] = await Promise.allSettled([
        chatbot.getSettings(),
        chatbot.getRules(),
        chatbot.getButtonMappings(),
      ])
      if (settingsResult.status === 'fulfilled') setSettings(settingsResult.value.data.settings)
      if (rulesResult.status === 'fulfilled') setRules(rulesResult.value.data.rules || [])
      if (mappingsResult.status === 'fulfilled') setMappings(mappingsResult.value.data.mappings || [])
    } catch (error) {
      console.error('Failed to fetch chatbot data:', error)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchTemplates = useCallback(async () => {
    if (templates.length > 0) return
    setTemplatesLoading(true)
    try {
      const res = await bulkMessage.templates()
      const tpls: WATemplate[] = (res.data?.templates || [])
        .filter((t: any) => t.status === 'APPROVED')
        .map((t: any) => ({ name: t.name, language: t.language, status: t.status }))
      setTemplates(tpls)
    } catch {
      // templates endpoint may fail if WA not configured
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

  const TemplateSelector = ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] text-primary focus:ring-0 focus:border-[var(--accent-border)]">
        <SelectValue placeholder={templatesLoading ? 'Loading templates...' : 'Select a template'} />
      </SelectTrigger>
      <SelectContent className="bg-[var(--bg-surface)] border-[var(--border-default)]">
        {templates.length === 0 && !templatesLoading && (
          <SelectItem value="_none" disabled>No approved templates found</SelectItem>
        )}
        {templates.map((t) => (
          <SelectItem key={`${t.name}-${t.language}`} value={t.name} className="text-primary">
            {t.name} ({t.language})
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )

  const filteredFallbackTemplates = templates.filter(
    (t) => t.name.toLowerCase().includes(fallbackSearch.toLowerCase()) || t.language.toLowerCase().includes(fallbackSearch.toLowerCase())
  )

  const FallbackTemplateSelector = () => {
    const currentLabel = settings.fallback_template_name
      ? templates.find((t) => t.name === settings.fallback_template_name)
        ? `${settings.fallback_template_name} (${templates.find((t) => t.name === settings.fallback_template_name)?.language})`
        : settings.fallback_template_name
      : null

    return (
      <div className="relative" ref={fallbackRef}>
        <button
          type="button"
          onClick={() => { setFallbackOpen((o) => !o); if (!fallbackOpen) setFallbackSearch('') }}
          className="flex h-10 w-full items-center justify-between rounded-lg border-[0.5px] border-[var(--border-default)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-primary focus:outline-none focus:ring-0 focus:border-[var(--accent-border)]"
        >
          <span className={currentLabel ? 'text-primary' : 'text-tertiary'}>
            {templatesLoading ? 'Loading templates...' : currentLabel ?? 'Select a template (or None)'}
          </span>
          <ChevronDown className={`h-4 w-4 opacity-50 transition-transform ${fallbackOpen ? 'rotate-180' : ''}`} />
        </button>

        {fallbackOpen && (
          <div className="absolute z-50 mt-1 w-full rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] shadow-lg" style={{ maxHeight: '280px', display: 'flex', flexDirection: 'column' }}>
            <div className="flex items-center border-b border-[var(--border-default)] px-3 py-2 gap-2">
              <Search className="h-4 w-4 text-tertiary shrink-0" />
              <input
                autoFocus
                className="flex-1 bg-transparent text-sm outline-none placeholder:text-hint text-primary"
                placeholder="Search template..."
                value={fallbackSearch}
                onChange={(e) => setFallbackSearch(e.target.value)}
              />
              {fallbackSearch && (
                <button onClick={() => setFallbackSearch('')} className="text-tertiary hover:text-primary">
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            <div className="overflow-y-auto flex-1 py-1">
              <button
                type="button"
                className={`flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-[var(--bg-hover)] ${!settings.fallback_template_name ? 'bg-[var(--bg-hover)] font-medium' : ''}`}
                onClick={() => { setSettings({ ...settings, fallback_template_name: '' }); setFallbackOpen(false); setFallbackSearch('') }}
              >
                <Ban className="h-3.5 w-3.5 text-tertiary" />
                <span className="text-tertiary italic">None - no fallback template</span>
              </button>

              <div className="mx-2 my-1 border-t border-[var(--border-default)]" />

              {templatesLoading && <p className="px-3 py-2 text-sm text-tertiary">Loading...</p>}
              {!templatesLoading && filteredFallbackTemplates.length === 0 && (
                <p className="px-3 py-2 text-sm text-tertiary">{templates.length === 0 ? 'No approved templates found' : 'No results'}</p>
              )}
              {filteredFallbackTemplates.map((t) => (
                <button
                  key={`${t.name}-${t.language}`}
                  type="button"
                  className={`flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-[var(--bg-hover)] ${settings.fallback_template_name === t.name ? 'bg-[var(--bg-hover)] font-medium' : ''}`}
                  onClick={() => { setSettings({ ...settings, fallback_template_name: t.name }); setFallbackOpen(false); setFallbackSearch('') }}
                >
                  <span className="inline-flex items-center justify-center w-4 h-4 rounded bg-[#25D366]/20 text-[#25D366] text-[10px] font-bold">T</span>
                  <span className="text-primary">{t.name}</span>
                  <span className="ml-auto text-xs text-tertiary">{t.language}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (fallbackRef.current && !fallbackRef.current.contains(e.target as Node)) {
        setFallbackOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

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

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col items-center text-center gap-3">
        <div>
          <p className="text-eyebrow mb-2">Automation & AI</p>
          <h1 className="text-section-title">WhatsApp Chatbot</h1>
          <p className="text-body text-[14px] mt-1">Configure auto-reply rules, button mappings, and fallback triggers</p>
        </div>
        <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshing} className="h-9">
          <RefreshCw className={`h-4 w-4 mr-1 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        {/* Settings Card */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2 text-[15px]">
                  <Bot className="h-5 w-5 text-[#25D366]" />
                  Chatbot Settings
                </CardTitle>
                <CardDescription>Configure auto-reply behavior and fallback trigger</CardDescription>
              </div>
              <Switch checked={isEnabled} onCheckedChange={handleToggle} />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center space-x-2 p-3 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-default)]">
              <Bot className={`h-5 w-5 ${isEnabled ? 'text-[#25D366]' : 'text-tertiary'}`} />
              <span className={isEnabled ? 'text-[#25D366] font-medium text-[13px]' : 'text-tertiary text-[13px]'}>
                {isEnabled ? 'Chatbot is active' : 'Chatbot is disabled'}
              </span>
            </div>

            {/* Fallback Template */}
            <div className="space-y-2">
              <Label htmlFor="fallbackTemplate" className="text-[13px] font-medium text-secondary">Fallback Template</Label>
              <FallbackTemplateSelector />
              <p className="text-xs text-tertiary">Sent when no button or keyword rule matches (rate-limited per contact)</p>
            </div>

            {/* Cooldown Hours */}
            <div className="space-y-2">
              <Label htmlFor="cooldownHours" className="text-[13px] font-medium text-secondary">Fallback Cooldown (hours)</Label>
              <Input
                id="cooldownHours"
                type="number"
                min={1}
                max={720}
                value={settings.fallback_cooldown_hours || 24}
                onChange={(e) => setSettings({ ...settings, fallback_cooldown_hours: parseInt(e.target.value) || 24 })}
                className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] placeholder:text-hint text-primary text-[14px] focus-visible:border-[var(--accent-border)] focus-visible:ring-0"
              />
              <p className="text-xs text-tertiary">How many hours to wait before sending the fallback template again to the same contact</p>
            </div>

            <Button onClick={handleSaveSettings} disabled={saving} className="w-full btn-pill h-11">
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
              Save Settings
            </Button>
          </CardContent>
        </Card>

        {/* Button Mappings Card */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2 text-[15px]">
                  <MousePointerClick className="h-5 w-5 text-purple-400" />
                  Button Mappings
                </CardTitle>
                <CardDescription>Map WhatsApp button clicks to template responses</CardDescription>
              </div>
              <Button size="sm" onClick={() => openMappingForm()} className="h-9">
                <Plus className="h-4 w-4 mr-1" /> Add
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {mappings.length === 0 && !showMappingForm && (
              <div className="text-center py-8 text-tertiary">
                <MousePointerClick className="h-10 w-10 mx-auto mb-2 opacity-20" />
                <p className="text-[14px]">No button mappings configured</p>
                <p className="text-xs mt-1">Click &quot;Add&quot; to map a button to a template</p>
              </div>
            )}

            {mappings.length > 0 && (
              <div className="space-y-2 mb-4">
                {mappings.map((m) => (
                  <div
                    key={m.id}
                    className={`flex items-center justify-between p-3 rounded-lg border ${m.is_active ? 'bg-[var(--bg-surface)] border-[var(--border-default)]' : 'bg-[var(--bg-hover)] border-[var(--border-subtle)] opacity-60'}`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        {m.button_text && (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20">
                            Text: {m.button_text}
                          </span>
                        )}
                        <span className="text-tertiary">→</span>
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-[#25D366]/10 text-[#25D366] border border-[#25D366]/20">
                          📋 {m.template_name}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 ml-2">
                      <Button variant="ghost" size="sm" onClick={() => openMappingForm(m)} className="h-8 w-8">
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => handleDeleteMapping(m.id)} className="h-8 w-8 text-red-400 hover:text-red-300 hover:bg-red-500/10">
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Mapping Form */}
            {showMappingForm && (
              <div className="border border-[var(--border-default)] rounded-lg p-4 space-y-3 bg-[var(--bg-surface)]">
                <div className="flex items-center justify-between">
                  <h4 className="font-medium text-[13px] text-primary">{editingMapping ? 'Edit Mapping' : 'New Button Mapping'}</h4>
                  <Button variant="ghost" size="sm" onClick={() => { setShowMappingForm(false); setEditingMapping(null) }} className="h-8 w-8">
                    <X className="h-4 w-4" />
                  </Button>
                </div>
                <div className="space-y-1">
                  <Label className="text-[12px] text-secondary">Button Text</Label>
                  <Input
                    placeholder="e.g. Morning"
                    value={mappingForm.button_text}
                    onChange={(e) => setMappingForm({ ...mappingForm, button_text: e.target.value })}
                    className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] placeholder:text-hint text-primary text-[14px] focus-visible:border-[var(--accent-border)] focus-visible:ring-0"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[12px] text-secondary">Template</Label>
                  <TemplateSelector value={mappingForm.template_name} onChange={(v) => setMappingForm({ ...mappingForm, template_name: v })} />
                </div>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" size="sm" onClick={() => { setShowMappingForm(false); setEditingMapping(null) }} className="h-9">Cancel</Button>
                  <Button size="sm" onClick={handleSaveMapping} disabled={savingMapping} className="h-9">
                    {savingMapping && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                    {editingMapping ? 'Update' : 'Create'}
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Keyword Rules Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2 text-[15px]">
                <Zap className="h-5 w-5 text-orange-400" />
                Keyword Automation Rules
              </CardTitle>
              <CardDescription>Auto-reply with text messages or templates when keywords are detected</CardDescription>
            </div>
            <Button size="sm" onClick={() => openRuleForm()} className="h-9">
              <Plus className="h-4 w-4 mr-1" /> Add Rule
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {rules.length === 0 && !showRuleForm && (
            <div className="text-center py-8 text-tertiary">
              <MessageSquare className="h-10 w-10 mx-auto mb-2 opacity-20" />
              <p className="text-[14px]">No keyword rules configured</p>
              <p className="text-xs mt-1">Click &quot;Add Rule&quot; to create an auto-reply rule</p>
            </div>
          )}

          {rules.length > 0 && (
            <div className="space-y-2 mb-4">
              {rules.map((r) => {
                const active = r.is_active === 1 || r.is_active === true
                return (
                  <div
                    key={r.id}
                    className={`flex items-center justify-between p-3 rounded-lg border ${active ? 'bg-[var(--bg-surface)] border-[var(--border-default)]' : 'bg-[var(--bg-hover)] border-[var(--border-subtle)] opacity-60'}`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-orange-500/10 text-orange-400 border border-orange-500/20">
                          {r.match_type || 'contains'}
                        </span>
                        <span className="font-mono text-[13px] font-medium text-primary">&quot;{r.keyword}&quot;</span>
                        <span className="text-tertiary">→</span>
                        {r.response_type === 'template' ? (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-[#25D366]/10 text-[#25D366] border border-[#25D366]/20">
                            📋 {r.response}
                          </span>
                        ) : (
                          <span className="text-[13px] text-secondary truncate max-w-[200px]">💬 {r.response}</span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 ml-2">
                      <Switch checked={active} onCheckedChange={() => handleToggleRule(r)} className="scale-75" />
                      <Button variant="ghost" size="sm" onClick={() => openRuleForm(r)} className="h-8 w-8">
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => handleDeleteRule(r.id)} className="h-8 w-8 text-red-400 hover:text-red-300 hover:bg-red-500/10">
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Rule Form */}
          {showRuleForm && (
            <div className="border border-[var(--border-default)] rounded-lg p-4 space-y-3 bg-[var(--bg-surface)]">
              <div className="flex items-center justify-between">
                <h4 className="font-medium text-[13px] text-primary">{editingRule ? 'Edit Rule' : 'New Keyword Rule'}</h4>
                <Button variant="ghost" size="sm" onClick={() => { setShowRuleForm(false); setEditingRule(null) }} className="h-8 w-8">
                  <X className="h-4 w-4" />
                </Button>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label className="text-[12px] text-secondary">Keyword</Label>
                  <Input
                    placeholder="e.g. pricing"
                    value={ruleForm.keyword}
                    onChange={(e) => setRuleForm({ ...ruleForm, keyword: e.target.value })}
                    className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] placeholder:text-hint text-primary text-[14px] focus-visible:border-[var(--accent-border)] focus-visible:ring-0"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[12px] text-secondary">Match Type</Label>
                  <Select value={ruleForm.match_type} onValueChange={(v) => setRuleForm({ ...ruleForm, match_type: v })}>
                    <SelectTrigger className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] text-primary focus:ring-0 focus:border-[var(--accent-border)]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-[var(--bg-surface)] border-[var(--border-default)]">
                      <SelectItem value="contains" className="text-primary">Contains</SelectItem>
                      <SelectItem value="exact" className="text-primary">Exact Match</SelectItem>
                      <SelectItem value="starts_with" className="text-primary">Starts With</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-1">
                <Label className="text-[12px] text-secondary">Response Type</Label>
                <Select value={ruleForm.response_type} onValueChange={(v) => setRuleForm({ ...ruleForm, response_type: v, response: '' })}>
                  <SelectTrigger className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] text-primary focus:ring-0 focus:border-[var(--accent-border)]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[var(--bg-surface)] border-[var(--border-default)]">
                    <SelectItem value="text" className="text-primary">💬 Text Message</SelectItem>
                    <SelectItem value="template" className="text-primary">📋 Template</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1">
                <Label className="text-[12px] text-secondary">{ruleForm.response_type === 'template' ? 'Template' : 'Response Text'}</Label>
                {ruleForm.response_type === 'template' ? (
                  <TemplateSelector value={ruleForm.response} onChange={(v) => setRuleForm({ ...ruleForm, response: v })} />
                ) : (
                  <Textarea
                    placeholder="Type your auto-reply message..."
                    value={ruleForm.response}
                    onChange={(e) => setRuleForm({ ...ruleForm, response: e.target.value })}
                    rows={3}
                    className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] placeholder:text-hint text-primary text-[14px] focus-visible:border-[var(--accent-border)] focus-visible:ring-0"
                  />
                )}
              </div>

              <div className="space-y-1">
                <Label className="text-[12px] text-secondary">Priority (higher = checked first)</Label>
                <Input
                  type="number"
                  min={0}
                  value={ruleForm.priority}
                  onChange={(e) => setRuleForm({ ...ruleForm, priority: parseInt(e.target.value) || 0 })}
                  className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] placeholder:text-hint text-primary text-[14px] focus-visible:border-[var(--accent-border)] focus-visible:ring-0"
                />
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => { setShowRuleForm(false); setEditingRule(null) }} className="h-9">Cancel</Button>
                <Button size="sm" onClick={handleSaveRule} disabled={savingRule} className="h-9">
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


