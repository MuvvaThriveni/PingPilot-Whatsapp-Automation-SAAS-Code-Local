'use client'

import { useState, useCallback, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useDropzone } from 'react-dropzone'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
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
      // Fallback: keep DEFAULT_MAX_VALID_CONTACTS (500)
      console.error('Failed to fetch limits, using default:', error)
    }
  }

  useEffect(() => {
    fetchCampaigns()
    fetchTemplates()
    fetchQuota()
    fetchLimits()
    // Periodic fallback to refresh the whole list (e.g., for status changes not caught by active poller)
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
          // Keep polling if it's running OR still scheduled
          if (campaign.status !== 'running' && campaign.status !== 'scheduled') {
            setActiveCampaignId(null)
            fetchQuota()
          }
        } catch (error) {
          console.error('Failed to fetch campaign status:', error)
          setActiveCampaignId(null) // stop on error
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
        // Store upload_id for cache-based /start (skip redundant parsing)
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

      // Pass upload_id so backend can skip redundant parsing
      if (uploadId) {
        formData.append('upload_id', uploadId)
      }

      if (isScheduled && scheduledAt) {
        // Convert to ISO string for backend
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
      case 'completed': return <CheckCircle className="h-4 w-4 text-green-500" />
      case 'running': return <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />
      case 'stopped': return <XCircle className="h-4 w-4 text-red-500" />
      case 'scheduled': return <Clock className="h-4 w-4 text-orange-500" />
      default: return <Clock className="h-4 w-4 text-gray-500" />
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Bulk WhatsApp Messaging</h1>
        <p className="text-gray-500 mt-1">Send messages to multiple contacts using Excel/CSV</p>
      </div>

      {/* Quota Bar */}
      {quota && (
        <Card>
          <CardContent className="pt-6 pb-4">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-gray-700">Monthly Message Quota</span>
                {quota.remaining <= 0 && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-700 border border-red-200">
                    <AlertTriangle className="h-3 w-3" />
                    Exhausted
                  </span>
                )}
              </div>
              <span className="text-sm text-gray-500">
                {quota.used} / {quota.limit} used &bull; {quota.remaining} remaining
              </span>
            </div>
            <div className="w-full bg-gray-100 rounded-full h-2.5">
              <div
                className={`h-2.5 rounded-full transition-all ${
                  quota.percent_used >= 100
                    ? 'bg-red-500'
                    : quota.percent_used >= 80
                    ? 'bg-orange-500'
                    : 'bg-green-500'
                }`}
                style={{ width: `${Math.min(quota.percent_used, 100)}%` }}
              />
            </div>
            <p className="text-xs text-gray-400 mt-1.5">
              Resets {new Date(quota.resets_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
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
          <CardContent className="space-y-6">
            {/* File Upload */}
            <div className="space-y-2">
              <Label>Contact File</Label>
              {!file ? (
                <div
                  {...getRootProps()}
                  className={`
                    border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors
                    ${isDragActive ? 'border-green-500 bg-green-50' : 'border-gray-300 hover:border-gray-400'}
                  `}
                >
                  <input {...getInputProps()} />
                  <Upload className="h-8 w-8 mx-auto text-gray-400 mb-2" />
                  <p className="text-sm text-gray-600">
                    {isDragActive ? 'Drop the file here' : 'Upload Excel or CSV file'}
                  </p>
                  <p className="text-xs text-gray-400 mt-1">
                    Columns: Name, Phone, ImageURL (optional)
                  </p>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between p-3 border rounded-lg bg-gray-50">
                    <div className="flex items-center space-x-3">
                      <FileSpreadsheet className="h-6 w-6 text-green-600" />
                      <div>
                        <p className="font-medium text-sm">{file.name}</p>
                        <p className="text-xs text-gray-500">
                          {parsing ? 'Parsing...' : `${totalContacts} valid contacts`}
                        </p>
                      </div>
                    </div>
                    <Button variant="ghost" size="icon" onClick={() => { setFile(null); setContacts([]); setTotalContacts(0); setUploadId(null); }}>
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                  {totalContacts > maxValidContacts && (
                    <div className="flex items-start gap-2 p-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-800">
                      <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                      <p>
                        File contains {totalContacts} valid contacts, exceeding the limit of {maxValidContacts} per campaign. Please upload a smaller file.
                      </p>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Template Name */}
            <div className="space-y-2">
              <Label htmlFor="template">WhatsApp Template</Label>
              {templates.length > 0 ? (
                <>
                  <select
                    id="template"
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
                  <p className="text-xs text-gray-500">
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
                  />
                  <p className="text-xs text-gray-500">
                    {loadingTemplates ? 'Loading templates...' : 'Enter template name or configure WhatsApp in Settings to load templates'}
                  </p>
                </>
              )}
            </div>

            {/* Campaign Name */}
            <div className="space-y-2">
              <Label htmlFor="campaignName">Campaign Name (Optional)</Label>
              <Input
                id="campaignName"
                placeholder="e.g., January Promo"
                value={campaignName}
                onChange={(e) => setCampaignName(e.target.value)}
              />
            </div>

            {/* Delay */}
            <div className="space-y-2">
              <Label htmlFor="delay">Delay Between Messages (ms)</Label>
              <Input
                id="delay"
                type="number"
                min="500"
                max="10000"
                value={delayMs}
                onChange={(e) => setDelayMs(e.target.value)}
              />
              <p className="text-xs text-gray-500">
                Recommended: 1000-2000ms to avoid rate limiting
              </p>
            </div>

            {/* Scheduling */}
            <div className="space-y-3 p-4 border rounded-lg bg-gray-50/50">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label className="text-base font-semibold">Schedule for later</Label>
                  <p className="text-sm text-gray-500">Pick a time to automatically start sending</p>
                </div>
                <Switch
                  checked={isScheduled}
                  onCheckedChange={setIsScheduled}
                />
              </div>

              {isScheduled && (
                <div className="space-y-2 pt-2 animate-in fade-in slide-in-from-top-2 duration-300">
                  <Input
                    type="datetime-local"
                    value={scheduledAt}
                    onChange={(e) => setScheduledAt(e.target.value)}
                    min={new Date().toISOString().slice(0, 16)}
                  />
                  <p className="text-xs text-orange-600 flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    Campaign status will remain &quot;Scheduled&quot; until the selected time.
                  </p>
                </div>
              )}
            </div>

            {/* Quota warning for this campaign */}
            {quota && totalContacts > 0 && totalContacts > quota.remaining && (
              <div className="flex items-start gap-2 p-3 rounded-lg bg-orange-50 border border-orange-200 text-sm text-orange-800">
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
              className="w-full"
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
                  Start Campaign {quota && totalContacts > 0 ? `(${totalContacts} / ${quota.remaining} remaining)` : ''}
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
                  <div key={contact.index} className="flex items-center justify-between p-2 bg-gray-50 rounded text-sm">
                    <div>
                      <p className="font-medium">{contact.name || 'No name'}</p>
                      <p className="text-gray-500">{contact.phone}</p>
                    </div>
                    {contact.imageUrl && (
                      <span className="text-xs text-blue-600">Has image</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-8 text-gray-500">
                <FileSpreadsheet className="h-12 w-12 mx-auto mb-3 text-gray-300" />
                <p>No contacts loaded</p>
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
                <div key={i} className="flex items-center justify-between p-4 border rounded-lg">
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
                    <Skeleton className="h-8 w-8 rounded-md" />
                  </div>
                </div>
              ))}
            </div>
          ) : campaigns.length > 0 ? (
            <div className="space-y-3">
              {campaigns.map((campaign) => (
                <div
                  key={campaign.campaign_id}
                  className="flex items-center justify-between p-4 border rounded-lg cursor-pointer hover:border-gray-400 hover:shadow-sm transition-all"
                  onClick={() => router.push(`/dashboard/bulk-message/${campaign.campaign_id}`)}
                >
                  <div className="flex items-center space-x-4">
                    {getStatusIcon(campaign.status)}
                    <div>
                      <p className="font-medium">{campaign.name}</p>
                      <p className="text-sm text-gray-500">
                        {campaign.status === 'scheduled' && campaign.scheduled_at ? (
                          <span className="text-orange-600 font-medium">
                            Scheduled: {new Date(campaign.scheduled_at).toLocaleString()}
                          </span>
                        ) : (
                          <>Template: {campaign.template_name.includes('|')
                            ? `${campaign.template_name.split('|')[0]} (${campaign.template_name.split('|')[1]})`
                            : campaign.template_name} • {new Date(campaign.created_at).toLocaleDateString()}</>
                        )}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center space-x-4">
                    <div className="text-right text-sm">
                      <p className="text-green-600">{campaign.sent_count} sent</p>
                      {campaign.failed_count > 0 && <p className="text-red-600">{campaign.failed_count} failed</p>}
                      {campaign.quota_exceeded_count > 0 && <p className="text-orange-500">{campaign.quota_exceeded_count} quota</p>}
                      {campaign.pending_count > 0 && campaign.status === 'running' && <p className="text-blue-500">{campaign.pending_count} pending</p>}
                    </div>
                    <div className="text-right text-sm text-gray-500">
                      {campaign.sent_count + campaign.failed_count + campaign.quota_exceeded_count + campaign.pending_count} / {campaign.total_contacts}
                    </div>
                    {campaign.status === 'running' && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(e) => { e.stopPropagation(); handleStopCampaign(campaign.campaign_id); }}
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
                        className="text-red-500 hover:text-red-700 hover:bg-red-50"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-gray-500">
              <p>No campaigns yet</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
