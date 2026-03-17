'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { bulkMessage } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import {
  ArrowLeft,
  CheckCircle,
  XCircle,
  Clock,
  Loader2,
  RefreshCw,
  Users,
  Send,
  AlertTriangle,
  Phone,
  User,
  Search,
} from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

interface Recipient {
  contact_phone: string
  contact_name: string
  status: string
  error_message: string
  attempt_count: number
  updated_at: string
}

interface CampaignDetail {
  campaign_id: string
  name: string
  template_name: string
  header_image_url: string
  total_contacts: number
  sent_count: number
  failed_count: number
  status: string
  delay_ms: number
  created_at: string
  scheduled_at: string | null
}

export default function CampaignDetailPage() {
  const params = useParams()
  const router = useRouter()
  const { toast } = useToast()
  const campaignId = params.campaignId as string

  const [campaign, setCampaign] = useState<CampaignDetail | null>(null)
  const [recipients, setRecipients] = useState<Recipient[]>([])
  const [loading, setLoading] = useState(true)
  const [resending, setResending] = useState(false)
  const [filter, setFilter] = useState<'all' | 'sent' | 'failed' | 'pending'>('all')
  const [search, setSearch] = useState('')

  const fetchDetails = useCallback(async () => {
    try {
      const res = await bulkMessage.details(campaignId)
      setCampaign(res.data.campaign)
      setRecipients(res.data.recipients)
    } catch (error: any) {
      toast({
        title: 'Error',
        description: error.response?.data?.error || 'Failed to load campaign details',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [campaignId, toast])

  useEffect(() => {
    fetchDetails()
  }, [fetchDetails])

  // Auto-refresh while campaign is running
  useEffect(() => {
    if (!campaign || campaign.status !== 'running') return
    const interval = setInterval(fetchDetails, 4000)
    return () => clearInterval(interval)
  }, [campaign?.status, fetchDetails])

  const handleResendFailed = async () => {
    setResending(true)
    try {
      const res = await bulkMessage.resendFailed(campaignId)
      toast({
        title: 'Resend started',
        description: res.data.message,
      })
      // Refresh details immediately so status updates to 'running' and auto-refresh kicks in
      await fetchDetails()
    } catch (error: any) {
      toast({
        title: 'Resend failed',
        description: error.response?.data?.error || 'Something went wrong',
        variant: 'destructive',
      })
    } finally {
      setResending(false)
    }
  }

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'sent':
      case 'submitted':
      case 'delivered':
      case 'read':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-green-50 text-green-700 border border-green-200">
            <CheckCircle className="h-3 w-3" />
            {status === 'submitted' ? 'Sent' : status.charAt(0).toUpperCase() + status.slice(1)}
          </span>
        )
      case 'failed':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-red-50 text-red-700 border border-red-200">
            <XCircle className="h-3 w-3" />
            Failed
          </span>
        )
      case 'pending':
      case 'queued':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-50 text-yellow-700 border border-yellow-200">
            <Clock className="h-3 w-3" />
            {status.charAt(0).toUpperCase() + status.slice(1)}
          </span>
        )
      case 'processing':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200">
            <Loader2 className="h-3 w-3 animate-spin" />
            Processing
          </span>
        )
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-gray-50 text-gray-600 border border-gray-200">
            {status}
          </span>
        )
    }
  }

  const getCampaignStatusBadge = (status: string) => {
    switch (status) {
      case 'completed':
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium bg-green-50 text-green-700 border border-green-200">
            <CheckCircle className="h-4 w-4" />
            Completed
          </span>
        )
      case 'running':
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium bg-blue-50 text-blue-700 border border-blue-200">
            <Loader2 className="h-4 w-4 animate-spin" />
            Running
          </span>
        )
      case 'stopped':
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium bg-red-50 text-red-700 border border-red-200">
            <XCircle className="h-4 w-4" />
            Stopped
          </span>
        )
      case 'scheduled':
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium bg-orange-50 text-orange-700 border border-orange-200">
            <Clock className="h-4 w-4" />
            Scheduled
          </span>
        )
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium bg-gray-50 text-gray-600 border border-gray-200">
            {status}
          </span>
        )
    }
  }

  const filteredRecipients = recipients.filter((r) => {
    const matchesFilter =
      filter === 'all' ||
      (filter === 'sent' && ['sent', 'submitted', 'delivered', 'read'].includes(r.status)) ||
      (filter === 'failed' && r.status === 'failed') ||
      (filter === 'pending' && ['pending', 'queued', 'processing'].includes(r.status))

    const matchesSearch =
      !search ||
      r.contact_phone.includes(search) ||
      r.contact_name.toLowerCase().includes(search.toLowerCase())

    return matchesFilter && matchesSearch
  })

  const sentCount = recipients.filter((r) =>
    ['sent', 'submitted', 'delivered', 'read'].includes(r.status)
  ).length
  const failedCount = recipients.filter((r) => r.status === 'failed').length
  const pendingCount = recipients.filter((r) =>
    ['pending', 'queued', 'processing'].includes(r.status)
  ).length

  if (loading) {
    return (
      <div className="space-y-6">
        {/* Header skeleton */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Skeleton className="h-10 w-10 rounded-md" />
            <div>
              <Skeleton className="h-7 w-48" />
              <Skeleton className="h-4 w-72 mt-1" />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Skeleton className="h-8 w-24 rounded-full" />
            <Skeleton className="h-9 w-24 rounded-md" />
          </div>
        </div>

        {/* Stats cards skeleton */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <Skeleton className="h-10 w-10 rounded-lg" />
                  <div>
                    <Skeleton className="h-7 w-12" />
                    <Skeleton className="h-3 w-20 mt-1" />
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Recipients table skeleton */}
        <Card>
          <CardHeader>
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-4 w-40 mt-1" />
          </CardHeader>
          <CardContent>
            <div className="border rounded-lg overflow-hidden">
              <div className="grid grid-cols-12 gap-4 px-4 py-3 bg-gray-50 border-b">
                <Skeleton className="col-span-1 h-4 w-6" />
                <Skeleton className="col-span-3 h-4 w-16" />
                <Skeleton className="col-span-3 h-4 w-14" />
                <Skeleton className="col-span-2 h-4 w-14" />
                <Skeleton className="col-span-3 h-4 w-14" />
              </div>
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="grid grid-cols-12 gap-4 px-4 py-3 items-center border-b last:border-b-0">
                  <Skeleton className="col-span-1 h-4 w-6" />
                  <div className="col-span-3 flex items-center gap-2">
                    <Skeleton className="h-8 w-8 rounded-full" />
                    <Skeleton className="h-4 w-20" />
                  </div>
                  <Skeleton className="col-span-3 h-4 w-28" />
                  <Skeleton className="col-span-2 h-6 w-16 rounded-full" />
                  <Skeleton className="col-span-3 h-4 w-20" />
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!campaign) {
    return (
      <div className="text-center py-20">
        <p className="text-gray-500">Campaign not found</p>
        <Button variant="outline" className="mt-4" onClick={() => router.push('/dashboard/bulk-message')}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Campaigns
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="outline" size="icon" onClick={() => router.push('/dashboard/bulk-message')}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{campaign.name}</h1>
            <p className="text-sm text-gray-500 mt-0.5">
              Template: {campaign.template_name} &bull; Created: {new Date(campaign.created_at).toLocaleString()}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {getCampaignStatusBadge(campaign.status)}
          <Button variant="outline" size="sm" onClick={fetchDetails}>
            <RefreshCw className="h-4 w-4 mr-1.5" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-blue-50 rounded-lg">
                <Users className="h-5 w-5 text-blue-600" />
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900">{campaign.total_contacts}</p>
                <p className="text-xs text-gray-500">Total Recipients</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-green-50 rounded-lg">
                <CheckCircle className="h-5 w-5 text-green-600" />
              </div>
              <div>
                <p className="text-2xl font-bold text-green-600">{sentCount}</p>
                <p className="text-xs text-gray-500">Sent</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-red-50 rounded-lg">
                <XCircle className="h-5 w-5 text-red-600" />
              </div>
              <div>
                <p className="text-2xl font-bold text-red-600">{failedCount}</p>
                <p className="text-xs text-gray-500">Failed</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-yellow-50 rounded-lg">
                <Clock className="h-5 w-5 text-yellow-600" />
              </div>
              <div>
                <p className="text-2xl font-bold text-yellow-600">{pendingCount}</p>
                <p className="text-xs text-gray-500">Pending</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Resend Failed Action */}
      {failedCount > 0 && campaign.status !== 'running' && (
        <Card className="border-red-200 bg-red-50/30">
          <CardContent className="pt-6">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-red-100 rounded-lg">
                  <AlertTriangle className="h-5 w-5 text-red-600" />
                </div>
                <div>
                  <p className="font-semibold text-gray-900">
                    {failedCount} message{failedCount !== 1 ? 's' : ''} failed to deliver
                  </p>
                  <p className="text-sm text-gray-500">
                    You can retry sending to all failed recipients
                  </p>
                </div>
              </div>
              <Button
                onClick={handleResendFailed}
                disabled={resending}
                className="bg-red-600 hover:bg-red-700 text-white"
              >
                {resending ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Resending...
                  </>
                ) : (
                  <>
                    <Send className="h-4 w-4 mr-2" />
                    Resend to Failed ({failedCount})
                  </>
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Recipients Table */}
      <Card>
        <CardHeader>
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div>
              <CardTitle>Recipients</CardTitle>
              <CardDescription>
                {filteredRecipients.length} of {recipients.length} recipients
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                <Input
                  placeholder="Search phone or name..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-9 w-64"
                />
              </div>
            </div>
          </div>
          {/* Filter Tabs */}
          <div className="flex gap-2 pt-2">
            {[
              { key: 'all' as const, label: 'All', count: recipients.length },
              { key: 'sent' as const, label: 'Sent', count: sentCount },
              { key: 'failed' as const, label: 'Failed', count: failedCount },
              { key: 'pending' as const, label: 'Pending', count: pendingCount },
            ].map((tab) => (
              <button
                key={tab.key}
                onClick={() => setFilter(tab.key)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  filter === tab.key
                    ? 'bg-gray-900 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {tab.label} ({tab.count})
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          {filteredRecipients.length > 0 ? (
            <div className="border rounded-lg overflow-hidden">
              {/* Table Header */}
              <div className="grid grid-cols-12 gap-4 px-4 py-3 bg-gray-50 border-b text-xs font-medium text-gray-500 uppercase tracking-wider">
                <div className="col-span-1">#</div>
                <div className="col-span-3">Contact</div>
                <div className="col-span-3">Phone</div>
                <div className="col-span-2">Status</div>
                <div className="col-span-3">Details</div>
              </div>
              {/* Table Body */}
              <div className="divide-y max-h-[500px] overflow-y-auto">
                {filteredRecipients.map((recipient, idx) => (
                  <div
                    key={recipient.contact_phone}
                    className="grid grid-cols-12 gap-4 px-4 py-3 items-center hover:bg-gray-50 transition-colors"
                  >
                    <div className="col-span-1 text-sm text-gray-400">{idx + 1}</div>
                    <div className="col-span-3">
                      <div className="flex items-center gap-2">
                        <div className="h-8 w-8 rounded-full bg-gray-100 flex items-center justify-center flex-shrink-0">
                          <User className="h-4 w-4 text-gray-400" />
                        </div>
                        <span className="text-sm font-medium text-gray-900 truncate">
                          {recipient.contact_name || 'Unknown'}
                        </span>
                      </div>
                    </div>
                    <div className="col-span-3">
                      <div className="flex items-center gap-1.5">
                        <Phone className="h-3.5 w-3.5 text-gray-400" />
                        <span className="text-sm text-gray-700 font-mono">{recipient.contact_phone}</span>
                      </div>
                    </div>
                    <div className="col-span-2">{getStatusBadge(recipient.status)}</div>
                    <div className="col-span-3">
                      {recipient.error_message ? (
                        <p className="text-xs text-red-600 truncate" title={recipient.error_message}>
                          {recipient.error_message}
                        </p>
                      ) : recipient.attempt_count > 0 ? (
                        <p className="text-xs text-gray-400">
                          {recipient.attempt_count} attempt{recipient.attempt_count !== 1 ? 's' : ''}
                        </p>
                      ) : (
                        <p className="text-xs text-gray-300">&mdash;</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="text-center py-12 text-gray-500">
              <Users className="h-10 w-10 mx-auto mb-3 text-gray-300" />
              <p>No recipients match the current filter</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
