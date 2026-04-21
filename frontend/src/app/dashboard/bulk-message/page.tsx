'use client'

import { useState, useCallback, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useDropzone } from 'react-dropzone'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { bulkMessage } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { Skeleton } from '@/components/ui/skeleton'
import { Upload, FileSpreadsheet, X, Play, Square, Loader2, CheckCircle, XCircle, Clock, Trash2, AlertTriangle } from 'lucide-react'
import axios from 'axios'

interface QuotaInfo {
  used: number
  limit: number
  remaining: number
  month_key: string
  resets_at: string
  percent_used: number
}

interface Contact {
  index: number
  name: string
  phone: string
  imageUrl: string
}

interface Template {
  name: string
  language: string
  display: string
  param_count: number
  status: string
}

interface Campaign {
  id: number
  campaign_id: string
  name: string
  template_name: string
  total_contacts: number
  sent_count: number
  failed_count: number
  pending_count: number
  quota_exceeded_count: number
  status: string
  created_at: string
  scheduled_at?: string
}

const DEFAULT_MAX_VALID_CONTACTS = 500

export default function BulkMessagePage() {
  const { toast } = useToast()
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [contacts, setContacts] = useState<Contact[]>([])
  const [totalContacts, setTotalContacts] = useState(0)
  const [templateName, setTemplateName] = useState('')
  const [campaignName, setCampaignName] = useState('')
  const [delayMs, setDelayMs] = useState('1000')
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [loadingCampaigns, setLoadingCampaigns] = useState(true)
  const [activeCampaignId, setActiveCampaignId] = useState<string | null>(null)
  const [isScheduled, setIsScheduled] = useState(false)
  const [scheduledAt, setScheduledAt] = useState('')
  const [templates, setTemplates] = useState<Template[]>([])
  const [loadingTemplates, setLoadingTemplates] = useState(false)
  const [quota, setQuota] = useState<QuotaInfo | null>(null)
  const [maxValidContacts, setMaxValidContacts] = useState(DEFAULT_MAX_VALID_CONTACTS)
  const [uploadId, setUploadId] = useState<string | null>(null)
  const selectedTemplate = templates.find(t => t.display === templateName)
  const templateHasParams = selectedTemplate && selectedTemplate.param_count > 0

  const fetchQuota = async () => {
    try {
      const res = await bulkMessage.quota()
      setQuota(res.data)
    } catch (error) {
      console.error('Failed to fetch quota:', error)
    }
  }

  const fetchLimits = async () => {
    try {
      const res = await bulkMessage.limits()
      if (res.data?.max_valid_contacts) {
        setMaxValidContacts(res.data.max_valid_contacts)
      }
    } catch (error) {
      console.error('Failed to fetch limits, using default:', error)
    }
  }

  useEffect(() => {
    fetchCampaigns()
    fetchTemplates()
    fetchQuota()
    fetchLimits()
    const interval = setInterval(() => { fetchCampaigns(); fetchQuota() }, 10000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    let interval: NodeJS.Timeout
    if (activeCampaignId) {
      interval = setInterval(async () => {
        try {
          const res = await bulkMessage.status(activeCampaignId)
          const campaign = res.data.campaign
          setCampaigns(prev => prev.map(c =>
            c.campaign_id === activeCampaignId ? campaign : c
          ))
          if (campaign.status !== 'running' && campaign.status !== 'scheduled') {
            setActiveCampaignId(null)
            fetchQuota()
          }
        } catch (error) {
          console.error('Failed to fetch campaign status:', error)
          setActiveCampaignId(null)
        }
      }, 3000)
    }
    return () => clearInterval(interval)
  }, [activeCampaignId])

  const fetchCampaigns = async () => {
    try {
      const res = await bulkMessage.campaigns()
      setCampaigns(res.data.campaigns)
    } catch (error) {
      console.error('Failed to fetch campaigns:', error)
    } finally {
      setLoadingCampaigns(false)
    }
  }

  const fetchTemplates = async () => {
    setLoadingTemplates(true)
    try {
      const res = await bulkMessage.templates()
      setTemplates(res.data.templates)
    } catch (error) {
      console.error('Failed to fetch templates:', error)
    } finally {
      setLoadingTemplates(false)
    }
  }

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    if (acceptedFiles.length > 0) {
      const selectedFile = acceptedFiles[0]
      setFile(selectedFile)
      setParsing(true)

      try {
        const formData = new FormData()
        formData.append('file', selectedFile)
        const res = await bulkMessage.parse(formData)
        setContacts(res.data.contacts)
        setTotalContacts(res.data.validContacts)
        setUploadId(res.data.upload_id || null)
        toast({ title: 'File parsed', description: `Found ${res.data.validContacts} valid contacts` })
      } catch (error: unknown) {
        toast({
          title: 'Parse failed',
          description: axios.isAxiosError(error) ? error.response?.data?.error || 'Failed to parse file' : 'Failed to parse file',
          variant: 'destructive'
        })
        setFile(null)
        setUploadId(null)
      } finally {
        setParsing(false)
      }
    }
  }, [toast])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/vnd.ms-excel': ['.xls'],
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
      'text/csv': ['.csv']
    },
    maxFiles: 1
  })

  const handleStartCampaign = async () => {
    if (!file || !templateName) {
      toast({ title: 'Error', description: 'Please upload a file and enter template name', variant: 'destructive' })
      return
    }

    setLoading(true)

    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('templateName', templateName)
      formData.append('campaignName', campaignName || `Campaign ${new Date().toLocaleDateString()}`)
      formData.append('delayMs', delayMs)

      if (uploadId) {
        formData.append('upload_id', uploadId)
      }

      if (isScheduled && scheduledAt) {
        formData.append('scheduledAt', new Date(scheduledAt).toISOString())
      }

      const res = await bulkMessage.start(formData)
      setActiveCampaignId(res.data.campaignId)

      toast({ title: 'Campaign started', description: `Sending to ${res.data.totalContacts} contacts` })

      fetchQuota()
      setFile(null)
      setContacts([])
      setTotalContacts(0)
      setUploadId(null)
      setTemplateName('')
      setCampaignName('')
      setIsScheduled(false)
      setScheduledAt('')

      fetchCampaigns()
    } catch (error: unknown) {
      let description = 'Something went wrong'
      if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail
        if (detail?.error === 'quota_exceeded') {
          description = `Monthly quota exhausted (${detail.used}/${detail.limit} used). Resets ${new Date(detail.resets_at).toLocaleDateString()}.`
        } else if (detail?.error === 'quota_would_exceed') {
          description = detail.message || `Campaign exceeds remaining quota (${detail.remaining} left).`
        } else {
          description = error.response?.data?.error || detail?.message || description
        }
      }
      toast({
        title: 'Failed to start campaign',
        description,
        variant: 'destructive'
      })
      fetchQuota()
    } finally {
      setLoading(false)
    }
  }

  const handleStopCampaign = async (campaignId: string) => {
    try {
      await bulkMessage.stop(campaignId)
      toast({ title: 'Stop requested', description: 'Campaign will stop shortly' })
    } catch (error) {
      toast({ title: 'Error', description: 'Failed to stop campaign', variant: 'destructive' })
    }
  }

  const handleDeleteCampaign = async (campaignId: string) => {
    try {
      await bulkMessage.deleteCampaign(campaignId)
      setCampaigns(prev => prev.filter(c => c.campaign_id !== campaignId))
      toast({ title: 'Campaign deleted' })
    } catch (error) {
      toast({ title: 'Error', description: 'Failed to delete campaign', variant: 'destructive' })
    }
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle className="h-4 w-4 text-[#25D366]" />
      case 'running': return <Loader2 className="h-4 w-4 text-blue-400 animate-spin" />
      case 'stopped': return <XCircle className="h-4 w-4 text-red-400" />
      case 'scheduled': return <Clock className="h-4 w-4 text-orange-400" />
      default: return <Clock className="h-4 w-4 text-tertiary" />
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="text-center max-w-3xl mx-auto">
        <p className="text-eyebrow mb-2">Marketing Automation</p>
        <h1 className="text-section-title">Bulk WhatsApp Messaging</h1>
        <p className="text-body text-[14px] mt-1">
          Send messages to multiple contacts using Excel or CSV files
        </p>
      </div>

      {/* Quota Bar */}
      {quota && (
        <Card>
          <CardContent className="pt-6 pb-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-medium text-primary">Monthly Message Quota</span>
                {quota.remaining <= 0 && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-red-500/10 text-red-400 border border-red-500/20">
                    <AlertTriangle className="h-3 w-3" />
                    Exhausted
                  </span>
                )}
              </div>
              <span className="text-[13px] text-secondary">
                {quota.used} / {quota.limit} used
              </span>
            </div>
            <div className="w-full bg-[var(--bg-hover)] rounded-full h-2">
              <div
                className={`h-2 rounded-full transition-all ${
                  quota.percent_used >= 100
                    ? 'bg-red-500'
                    : quota.percent_used >= 80
                    ? 'bg-orange-400'
                    : 'bg-[#25D366]'
                }`}
                style={{ width: `${Math.min(quota.percent_used, 100)}%` }}
              />
            </div>
            <p className="text-[12px] text-tertiary mt-2">
              {quota.remaining} remaining - Resets {new Date(quota.resets_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
            </p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Upload & Configure */}
        <Card>
          <CardHeader>
            <CardTitle>New Campaign</CardTitle>
            <CardDescription>Upload contacts and configure your campaign</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {/* File Upload */}
            <div className="space-y-2">
              <Label className="text-[13px] font-medium text-secondary">Contact File</Label>
              {!file ? (
                <div
                  {...getRootProps()}
                  className={`
                    border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-apple
                    ${isDragActive ? 'border-[#25D366] bg-[var(--accent-dim)]' : 'border-[var(--border-default)] hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)]'}
                  `}
                >
                  <input {...getInputProps()} />
                  <Upload className="h-8 w-8 mx-auto text-tertiary mb-2" />
                  <p className="text-sm text-secondary">
                    {isDragActive ? 'Drop the file here' : 'Upload Excel or CSV file'}
                  </p>
                  <p className="text-xs text-tertiary mt-1">
                    Columns: Name, Phone, ImageURL (optional)
                  </p>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between p-3 bg-[var(--bg-surface)] rounded-xl border-[0.5px] border-[var(--border-default)]">
                    <div className="flex items-center space-x-3">
                      <FileSpreadsheet className="h-6 w-6 text-[#25D366]" />
                      <div>
                        <p className="font-medium text-[13px] text-primary">{file.name}</p>
                        <p className="text-xs text-tertiary">
                          {parsing ? 'Parsing...' : `${totalContacts} valid contacts`}
                        </p>
                      </div>
                    </div>
                    <Button variant="ghost" size="icon" onClick={() => { setFile(null); setContacts([]); setTotalContacts(0); setUploadId(null); }} className="h-8 w-8">
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                  {totalContacts > maxValidContacts && (
                    <div className="flex items-start gap-2 p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-[13px] text-red-400">
                      <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                      <p>
                        File contains {totalContacts} valid contacts, exceeding the limit of {maxValidContacts} per campaign.
                      </p>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Template Name */}
            <div className="space-y-2">
              <Label htmlFor="template" className="text-[13px] font-medium text-secondary">WhatsApp Template</Label>
              {templates.length > 0 ? (
                <>
                  <select
                    id="template"
                    className="flex h-10 w-full rounded-lg border-[0.5px] border-[var(--border-default)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-primary ring-offset-background focus-visible:border-[var(--accent-border)] focus-visible:outline-none focus-visible:ring-0"
                    value={templateName}
                    onChange={(e) => setTemplateName(e.target.value)}
                  >
                    <option value="">Select a template...</option>
                    {templates.filter(t => t.param_count === 0).map((t) => (
                      <option key={t.display} value={t.display}>
                        {t.name} ({t.language})
                      </option>
                    ))}
                    {templates.some(t => t.param_count > 0) && (
                      <optgroup label="Templates with parameters (may fail)">
                        {templates.filter(t => t.param_count > 0).map((t) => (
                          <option key={t.display} value={t.display}>
                            {t.name} ({t.language}) — {t.param_count} param(s)
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                  <p className="text-xs text-tertiary">
                    Templates without parameters are recommended for bulk sending
                  </p>
                </>
              ) : (
                <>
                  <Input
                    id="template"
                    placeholder="e.g., hello_world|en_US"
                    value={templateName}
                    onChange={(e) => setTemplateName(e.target.value)}
                    className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] placeholder:text-hint text-primary text-[14px] focus-visible:border-[var(--accent-border)] focus-visible:ring-0"
                  />
                  <p className="text-xs text-tertiary">
                    {loadingTemplates ? 'Loading templates...' : 'Enter template name or configure WhatsApp in Settings to load templates'}
                  </p>
                </>
              )}
            </div>

            {/* Campaign Name */}
            <div className="space-y-2">
              <Label htmlFor="campaignName" className="text-[13px] font-medium text-secondary">Campaign Name (Optional)</Label>
              <Input
                id="campaignName"
                placeholder="e.g., January Promo"
                value={campaignName}
                onChange={(e) => setCampaignName(e.target.value)}
                className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] placeholder:text-hint text-primary text-[14px] focus-visible:border-[var(--accent-border)] focus-visible:ring-0"
              />
            </div>

            {/* Delay */}
            <div className="space-y-2">
              <Label htmlFor="delay" className="text-[13px] font-medium text-secondary">Delay Between Messages (ms)</Label>
              <Input
                id="delay"
                type="number"
                min="500"
                max="10000"
                value={delayMs}
                onChange={(e) => setDelayMs(e.target.value)}
                className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] placeholder:text-hint text-primary text-[14px] focus-visible:border-[var(--accent-border)] focus-visible:ring-0"
              />
              <p className="text-xs text-tertiary">
                Recommended: 1000-2000ms to avoid rate limiting
              </p>
            </div>

            {/* Scheduling */}
            <div className="space-y-3 p-4 border-[0.5px] border-[var(--border-default)] rounded-xl bg-[var(--bg-surface)]">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label className="text-[13px] font-semibold text-primary">Schedule for later</Label>
                  <p className="text-xs text-secondary">Pick a time to automatically start sending</p>
                </div>
                <Switch
                  checked={isScheduled}
                  onCheckedChange={setIsScheduled}
                />
              </div>

              {isScheduled && (
                <div className="space-y-2 pt-2">
                  <Input
                    type="datetime-local"
                    value={scheduledAt}
                    onChange={(e) => setScheduledAt(e.target.value)}
                    min={new Date().toISOString().slice(0, 16)}
                    className="bg-[var(--bg-surface)] border-[0.5px] border-[var(--border-default)] placeholder:text-hint text-primary text-[14px] focus-visible:border-[var(--accent-border)] focus-visible:ring-0"
                  />
                  <p className="text-xs text-orange-400 flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    Campaign status will remain &quot;Scheduled&quot; until the selected time.
                  </p>
                </div>
              )}
            </div>

            {/* Quota warning */}
            {quota && totalContacts > 0 && totalContacts > quota.remaining && (
              <div className="flex items-start gap-2 p-3 rounded-xl bg-orange-500/10 border border-orange-500/20 text-[13px] text-orange-400">
                <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <div>
                  {quota.remaining <= 0 ? (
                    <p>Monthly quota exhausted. You cannot start a campaign until it resets on {new Date(quota.resets_at).toLocaleDateString()}.</p>
                  ) : (
                    <p>This campaign has {totalContacts} contacts but only {quota.remaining} messages remaining this month.</p>
                  )}
                </div>
              </div>
            )}

            {/* Start Button */}
            <Button
              className="w-full btn-pill h-11"
              onClick={handleStartCampaign}
              disabled={loading || !file || !templateName || totalContacts > maxValidContacts || (quota !== null && quota.remaining <= 0) || (quota !== null && totalContacts > quota.remaining)}
            >
              {loading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <Play className="mr-2 h-4 w-4" />
                  Start Campaign {quota && totalContacts > 0 && quota.remaining > 0 ? `(${totalContacts} / ${quota.remaining} remaining)` : ''}
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        {/* Contact Preview */}
        <Card>
          <CardHeader>
            <CardTitle>Contact Preview</CardTitle>
            <CardDescription>
              {contacts.length > 0 ? `Showing first ${Math.min(contacts.length, 10)} of ${totalContacts} contacts (max ${maxValidContacts})` : 'Upload a file to preview contacts'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {contacts.length > 0 ? (
              <div className="space-y-2 max-h-80 overflow-y-auto">
                {contacts.slice(0, 10).map((contact) => (
                  <div key={contact.index} className="flex items-center justify-between p-3 bg-[var(--bg-surface)] rounded-xl border-[0.5px] border-[var(--border-default)]">
                    <div>
                      <p className="font-medium text-[13px] text-primary">{contact.name || 'No name'}</p>
                      <p className="text-xs text-tertiary">{contact.phone}</p>
                    </div>
                    {contact.imageUrl && (
                      <span className="text-[11px] text-blue-400">Has image</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-12 text-tertiary">
                <FileSpreadsheet className="h-12 w-12 mx-auto mb-3 opacity-20" />
                <p className="text-[14px]">No contacts loaded</p>
                <p className="text-xs mt-1">Upload a file to see preview</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Campaign History */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Campaigns</CardTitle>
          <CardDescription>Track your campaign progress and history</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingCampaigns ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex items-center justify-between p-4 border border-[var(--border-default)] rounded-xl">
                  <div className="flex items-center space-x-4">
                    <Skeleton className="h-4 w-4 rounded-full" />
                    <div>
                      <Skeleton className="h-5 w-36" />
                      <Skeleton className="h-4 w-52 mt-1" />
                    </div>
                  </div>
                  <div className="flex items-center space-x-4">
                    <div className="text-right">
                      <Skeleton className="h-4 w-14" />
                      <Skeleton className="h-4 w-14 mt-1" />
                    </div>
                    <Skeleton className="h-4 w-12" />
                    <Skeleton className="h-8 w-8 rounded-lg" />
                  </div>
                </div>
              ))}
            </div>
          ) : campaigns.length > 0 ? (
            <div className="space-y-3">
              {campaigns.map((campaign) => (
                <div
                  key={campaign.campaign_id}
                  className="flex items-center justify-between p-4 border border-[var(--border-default)] rounded-xl cursor-pointer hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)] transition-apple"
                  onClick={() => router.push(`/dashboard/bulk-message/${campaign.campaign_id}`)}
                >
                  <div className="flex items-center space-x-4">
                    {getStatusIcon(campaign.status)}
                    <div>
                      <p className="font-medium text-[14px] text-primary">{campaign.name}</p>
                      <p className="text-xs text-secondary">
                        {campaign.status === 'scheduled' && campaign.scheduled_at ? (
                          <span className="text-orange-400 font-medium">
                            Scheduled: {new Date(campaign.scheduled_at).toLocaleString()}
                          </span>
                        ) : (
                          <>Template: {campaign.template_name} - {new Date(campaign.created_at).toLocaleDateString()}</>
                        )}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center space-x-4">
                    <div className="text-right text-[13px]">
                      <p className="text-[#25D366]">{campaign.sent_count} sent</p>
                      {campaign.failed_count > 0 && <p className="text-red-400">{campaign.failed_count} failed</p>}
                      {campaign.quota_exceeded_count > 0 && <p className="text-orange-400">{campaign.quota_exceeded_count} quota</p>}
                      {campaign.pending_count > 0 && campaign.status === 'running' && <p className="text-blue-400">{campaign.pending_count} pending</p>}
                    </div>
                    <div className="text-right text-[13px] text-tertiary">
                      {campaign.sent_count + campaign.failed_count + campaign.quota_exceeded_count + campaign.pending_count} / {campaign.total_contacts}
                    </div>
                    {campaign.status === 'running' && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(e) => { e.stopPropagation(); handleStopCampaign(campaign.campaign_id); }}
                        className="h-8"
                      >
                        <Square className="h-3 w-3 mr-1" />
                        Stop
                      </Button>
                    )}
                    {campaign.status !== 'running' && (
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={(e) => { e.stopPropagation(); handleDeleteCampaign(campaign.campaign_id); }}
                        className="text-red-400 hover:text-red-300 hover:bg-red-500/10 h-8 w-8"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-12 text-tertiary">
              <p className="text-[14px]">No campaigns yet</p>
              <p className="text-xs mt-1">Start your first campaign above</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

