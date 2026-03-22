'use client'

import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { logs } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { Skeleton } from '@/components/ui/skeleton'
import { Download, RefreshCw, CheckCircle, XCircle, Clock, Loader2 } from 'lucide-react'

interface LogEntry {
  id: number
  product_type: string
  recipient: string
  message_id: string | null
  template_name: string | null
  status: string
  error_message: string | null
  campaign_id: string | null
  created_at: string
}

export default function LogsPage() {
  const { toast } = useToast()
  const [loading, setLoading] = useState(true)
  const [logEntries, setLogEntries] = useState<LogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [filters, setFilters] = useState({
    product_type: '',
    status: '',
    limit: 50,
    offset: 0
  })

  const fetchLogs = useCallback(async () => {
    setLoading(true)
    try {
      const params: { limit: number; offset: number; product_type?: string; status?: string } = { limit: filters.limit, offset: filters.offset }
      if (filters.product_type) params.product_type = filters.product_type
      if (filters.status) params.status = filters.status
      
      const res = await logs.get(params)
      setLogEntries(res.data.logs)
      setTotal(res.data.total)
    } catch (error) {
      console.error('Failed to fetch logs:', error)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => {
    fetchLogs()
  }, [fetchLogs])

  const handleExport = async () => {
    try {
      const params: { product_type?: string; status?: string } = {}
      if (filters.product_type) params.product_type = filters.product_type
      if (filters.status) params.status = filters.status
      
      const res = await logs.export(params)
      const blob = new Blob([res.data], { type: 'text/csv' })
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `wappflow-logs-${new Date().toISOString().split('T')[0]}.csv`
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
      toast({ title: 'Export complete', description: 'Logs downloaded successfully' })
    } catch (error) {
      toast({ title: 'Export failed', description: 'Failed to export logs', variant: 'destructive' })
    }
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'sent':
      case 'delivered':
        return <CheckCircle className="h-4 w-4 text-green-500" />
      case 'failed':
        return <XCircle className="h-4 w-4 text-red-500" />
      default:
        return <Clock className="h-4 w-4 text-gray-400" />
    }
  }

  const getProductLabel = (type: string) => {
    switch (type) {
      case 'file_forward': return 'Live Messaging'
      case 'bulk_message': return 'Bulk Message'
      case 'chatbot': return 'Chatbot'
      default: return type
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Message Logs</h1>
          <p className="text-gray-500 mt-1">View and export your message history</p>
        </div>
        <div className="flex space-x-2">
          <Button variant="outline" onClick={fetchLogs} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button variant="outline" onClick={handleExport}>
            <Download className="h-4 w-4 mr-2" />
            Export CSV
          </Button>
        </div>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap gap-4">
            <div className="w-48">
              <Select
                value={filters.product_type}
                onValueChange={(value) => setFilters({ ...filters, product_type: value, offset: 0 })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="All Products" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">All Products</SelectItem>
                  <SelectItem value="file_forward">Live Messaging</SelectItem>
                  <SelectItem value="bulk_message">Bulk Message</SelectItem>
                  <SelectItem value="chatbot">Chatbot</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="w-48">
              <Select
                value={filters.status}
                onValueChange={(value) => setFilters({ ...filters, status: value, offset: 0 })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="All Statuses" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">All Statuses</SelectItem>
                  <SelectItem value="sent">Sent</SelectItem>
                  <SelectItem value="delivered">Delivered</SelectItem>
                  <SelectItem value="failed">Failed</SelectItem>
                  <SelectItem value="pending">Pending</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="text-sm text-gray-500 flex items-center">
              Showing {logEntries.length} of {total} logs
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Logs Table */}
      <Card>
        <CardHeader>
          <CardTitle>Message History</CardTitle>
          <CardDescription>All messages sent through WappFlow</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Status</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Product</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Recipient</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Template</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Time</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: 8 }).map((_, i) => (
                    <tr key={i} className="border-b">
                      <td className="py-3 px-2"><div className="flex items-center space-x-2"><Skeleton className="h-4 w-4 rounded-full" /><Skeleton className="h-4 w-14" /></div></td>
                      <td className="py-3 px-2"><Skeleton className="h-6 w-20 rounded" /></td>
                      <td className="py-3 px-2"><Skeleton className="h-4 w-28" /></td>
                      <td className="py-3 px-2"><Skeleton className="h-4 w-24" /></td>
                      <td className="py-3 px-2"><Skeleton className="h-4 w-32" /></td>
                      <td className="py-3 px-2"><Skeleton className="h-4 w-20" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : logEntries.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Status</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Product</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Recipient</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Template</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Time</th>
                    <th className="text-left py-3 px-2 font-medium text-gray-500">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {logEntries.map((log) => (
                    <tr key={log.id} className="border-b hover:bg-gray-50">
                      <td className="py-3 px-2">
                        <div className="flex items-center space-x-2">
                          {getStatusIcon(log.status)}
                          <span className="capitalize">{log.status}</span>
                        </div>
                      </td>
                      <td className="py-3 px-2">
                        <span className="px-2 py-1 bg-gray-100 rounded text-xs">
                          {getProductLabel(log.product_type)}
                        </span>
                      </td>
                      <td className="py-3 px-2 font-mono text-xs">{log.recipient}</td>
                      <td className="py-3 px-2 text-gray-500">{log.template_name || '-'}</td>
                      <td className="py-3 px-2 text-gray-500">
                        {new Date(log.created_at).toLocaleString()}
                      </td>
                      <td className="py-3 px-2 text-red-500 text-xs max-w-xs truncate">
                        {log.error_message || '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-12 text-gray-500">
              <p>No logs found</p>
              <p className="text-sm mt-1">Messages will appear here once you start sending</p>
            </div>
          )}

          {/* Pagination */}
          {total > filters.limit && (
            <div className="flex items-center justify-between mt-4 pt-4 border-t">
              <Button
                variant="outline"
                size="sm"
                disabled={filters.offset === 0}
                onClick={() => setFilters({ ...filters, offset: Math.max(0, filters.offset - filters.limit) })}
              >
                Previous
              </Button>
              <span className="text-sm text-gray-500">
                Page {Math.floor(filters.offset / filters.limit) + 1} of {Math.ceil(total / filters.limit)}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={filters.offset + filters.limit >= total}
                onClick={() => setFilters({ ...filters, offset: filters.offset + filters.limit })}
              >
                Next
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
