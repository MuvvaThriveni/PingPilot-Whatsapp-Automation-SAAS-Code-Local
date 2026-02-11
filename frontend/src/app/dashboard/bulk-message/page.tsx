'use client'

import { useState, useCallback, useEffect } from 'react'
import { useDropzone } from 'react-dropzone'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { bulkMessage } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { Upload, FileSpreadsheet, X, Play, Square, Loader2, CheckCircle, XCircle, Clock, Trash2 } from 'lucide-react'

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
  status: string
  created_at: string
}

export default function BulkMessagePage() {
  const { toast } = useToast()
  const [loading, setLoading] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [contacts, setContacts] = useState<Contact[]>([])
  const [totalContacts, setTotalContacts] = useState(0)
  const [templateName, setTemplateName] = useState('')
  const [campaignName, setCampaignName] = useState('')
  const [delayMs, setDelayMs] = useState('1000')
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [activeCampaignId, setActiveCampaignId] = useState<string | null>(null)
  const [templates, setTemplates] = useState<Template[]>([])
  const [loadingTemplates, setLoadingTemplates] = useState(false)
  const [headerImageUrl, setHeaderImageUrl] = useState('')
  const selectedTemplate = templates.find(t => t.display === templateName)
  const templateHasParams = selectedTemplate && selectedTemplate.param_count > 0

  useEffect(() => {
    fetchCampaigns()
    fetchTemplates()
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
          if (campaign.status !== 'running') {
            setActiveCampaignId(null)
          }
        } catch (error) {
          console.error('Failed to fetch campaign status:', error)
        }
      }, 2000)
    }
    return () => clearInterval(interval)
  }, [activeCampaignId])

  const fetchCampaigns = async () => {
    try {
      const res = await bulkMessage.campaigns()
      setCampaigns(res.data.campaigns)
    } catch (error) {
      console.error('Failed to fetch campaigns:', error)
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
        toast({ title: 'File parsed', description: `Found ${res.data.validContacts} valid contacts` })
      } catch (error: any) {
        toast({
          title: 'Parse failed',
          description: error.response?.data?.error || 'Failed to parse file',
          variant: 'destructive'
        })
        setFile(null)
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
    
    // Require image URL for templates with parameters
    if (templateHasParams && !headerImageUrl.trim()) {
      toast({ title: 'Error', description: 'Please provide a header image URL for this template', variant: 'destructive' })
      return
    }

    setLoading(true)

    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('templateName', templateName)
      formData.append('campaignName', campaignName || `Campaign ${new Date().toLocaleDateString()}`)
      formData.append('delayMs', delayMs)
      if (headerImageUrl) {
        formData.append('headerImageUrl', headerImageUrl)
      }

      const res = await bulkMessage.start(formData)
      setActiveCampaignId(res.data.campaignId)
      
      toast({ title: 'Campaign started', description: `Sending to ${res.data.totalContacts} contacts` })
      
      setFile(null)
      setContacts([])
      setTotalContacts(0)
      setTemplateName('')
      setCampaignName('')
      setHeaderImageUrl('')
      
      fetchCampaigns()
    } catch (error: any) {
      toast({
        title: 'Failed to start campaign',
        description: error.response?.data?.error || 'Something went wrong',
        variant: 'destructive'
      })
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
      default: return <Clock className="h-4 w-4 text-gray-500" />
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Bulk WhatsApp Messaging</h1>
        <p className="text-gray-500 mt-1">Send messages to multiple contacts using Excel/CSV</p>
      </div>

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
                  <Button variant="ghost" size="icon" onClick={() => { setFile(null); setContacts([]); setTotalContacts(0); }}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
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

            {/* Header Image URL for templates with IMAGE header */}
            {templateHasParams && (
              <div className="space-y-2">
                <Label htmlFor="headerImageUrl">Header Image URL (Required)</Label>
                <Input
                  id="headerImageUrl"
                  placeholder="https://example.com/image.jpg"
                  value={headerImageUrl}
                  onChange={(e) => setHeaderImageUrl(e.target.value)}
                />
                <p className="text-xs text-amber-600">
                  This template requires an image. Provide a publicly accessible image URL.
                </p>
              </div>
            )}

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

            {/* Start Button */}
            <Button 
              className="w-full" 
              onClick={handleStartCampaign}
              disabled={loading || !file || !templateName}
            >
              {loading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <Play className="mr-2 h-4 w-4" />
                  Start Campaign
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
              {contacts.length > 0 ? `Showing first ${Math.min(contacts.length, 10)} of ${totalContacts} contacts` : 'Upload a file to preview contacts'}
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
          {campaigns.length > 0 ? (
            <div className="space-y-3">
              {campaigns.map((campaign) => (
                <div key={campaign.campaign_id} className="flex items-center justify-between p-4 border rounded-lg">
                  <div className="flex items-center space-x-4">
                    {getStatusIcon(campaign.status)}
                    <div>
                      <p className="font-medium">{campaign.name}</p>
                      <p className="text-sm text-gray-500">
                        Template: {campaign.template_name} • {new Date(campaign.created_at).toLocaleDateString()}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center space-x-4">
                    <div className="text-right text-sm">
                      <p className="text-green-600">{campaign.sent_count} sent</p>
                      <p className="text-red-600">{campaign.failed_count} failed</p>
                    </div>
                    <div className="text-right text-sm text-gray-500">
                      {campaign.sent_count + campaign.failed_count} / {campaign.total_contacts}
                    </div>
                    {campaign.status === 'running' && (
                      <Button 
                        variant="outline" 
                        size="sm"
                        onClick={() => handleStopCampaign(campaign.campaign_id)}
                      >
                        <Square className="h-3 w-3 mr-1" />
                        Stop
                      </Button>
                    )}
                    {campaign.status !== 'running' && (
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleDeleteCampaign(campaign.campaign_id)}
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
