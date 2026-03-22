'use client'

import { useState, useEffect } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { settings } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { Skeleton } from '@/components/ui/skeleton'
import { Settings, CheckCircle, XCircle, Loader2, Eye, EyeOff, ExternalLink } from 'lucide-react'
import axios from 'axios'

export default function SettingsPage() {
  const { toast } = useToast()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [showToken, setShowToken] = useState(false)
  const [connectionStatus, setConnectionStatus] = useState<'unknown' | 'success' | 'error'>('unknown')
  const [connectionInfo, setConnectionInfo] = useState<{ phoneNumber?: string; verifiedName?: string } | null>(null)

  const [formData, setFormData] = useState({
    business_account_id: '',
    phone_number_id: '',
    access_token: '',
    webhook_verify_token: ''
  })
  const [hasExistingToken, setHasExistingToken] = useState(false)

  useEffect(() => {
    fetchSettings()
  }, [])

  const fetchSettings = async () => {
    try {
      const res = await settings.getWhatsApp()
      if (res.data.settings) {
        setFormData({
          business_account_id: res.data.settings.business_account_id || '',
          phone_number_id: res.data.settings.phone_number_id || '',
          access_token: '',   // never pre-fill token in UI for security
          webhook_verify_token: res.data.settings.webhook_verify_token || ''
        })
        // Mark whether a token already exists so we don't require re-entry
        setHasExistingToken(res.data.settings.is_configured === true)
      }
    } catch (error) {
      console.error('Failed to fetch settings:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    // Token required only if there's no existing token saved
    if (!formData.business_account_id || !formData.phone_number_id) {
      toast({ title: 'Error', description: 'Please fill in Business Account ID and Phone Number ID', variant: 'destructive' })
      return
    }
    if (!hasExistingToken && !formData.access_token.trim()) {
      toast({ title: 'Error', description: 'Access token is required for first-time setup', variant: 'destructive' })
      return
    }

    setSaving(true)
    try {
      // Only send access_token if user typed a new one — otherwise backend keeps existing
      const payload: {
        business_account_id: string
        phone_number_id: string
        webhook_verify_token: string
        access_token?: string
      } = {
        business_account_id: formData.business_account_id,
        phone_number_id: formData.phone_number_id,
        webhook_verify_token: formData.webhook_verify_token,
      }
      if (formData.access_token.trim()) {
        payload.access_token = formData.access_token.trim()
      }
      await settings.saveWhatsApp(payload)
      toast({ title: 'Settings saved', description: 'WhatsApp configuration updated successfully' })
      setConnectionStatus('unknown')
      setHasExistingToken(true)
      setFormData(prev => ({ ...prev, access_token: '' }))  // clear field after save
    } catch (error: unknown) {
      toast({
        title: 'Failed to save',
        description: axios.isAxiosError(error) ? error.response?.data?.error || 'Something went wrong' : 'Something went wrong',
        variant: 'destructive'
      })
    } finally {
      setSaving(false)
    }
  }

  const handleTestConnection = async () => {
    setTesting(true)
    setConnectionStatus('unknown')
    try {
      const res = await settings.testConnection()
      if (res.data.success) {
        setConnectionStatus('success')
        setConnectionInfo({
          phoneNumber: res.data.phoneNumber,
          verifiedName: res.data.verifiedName
        })
        toast({ title: 'Connection successful', description: `Connected to ${res.data.verifiedName}` })
      } else {
        setConnectionStatus('error')
        toast({ title: 'Connection failed', description: res.data.error, variant: 'destructive' })
      }
    } catch (error: unknown) {
      setConnectionStatus('error')
      toast({
        title: 'Connection failed',
        description: axios.isAxiosError(error) ? error.response?.data?.error || 'Failed to connect to WhatsApp API' : 'Failed to connect to WhatsApp API',
        variant: 'destructive'
      })
    } finally {
      setTesting(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <div>
          <Skeleton className="h-7 w-48" />
          <Skeleton className="h-4 w-72 mt-2" />
        </div>
        <Card>
          <CardHeader>
            <div className="flex items-center space-x-3">
              <Skeleton className="h-10 w-10 rounded-lg" />
              <div>
                <Skeleton className="h-5 w-40" />
                <Skeleton className="h-4 w-56 mt-1" />
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="space-y-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-10 w-full rounded-md" />
              </div>
            ))}
            <div className="flex space-x-3 pt-2">
              <Skeleton className="h-10 w-full rounded-md" />
              <Skeleton className="h-10 w-full rounded-md" />
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-500 mt-1">Configure your WhatsApp Business API connection</p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-green-100 rounded-lg">
              <Settings className="h-5 w-5 text-green-600" />
            </div>
            <div>
              <CardTitle>WhatsApp Business API</CardTitle>
              <CardDescription>Connect your WhatsApp Business account to send messages</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Connection Status */}
          {connectionStatus !== 'unknown' && (
            <div className={`flex items-center space-x-3 p-4 rounded-lg ${connectionStatus === 'success' ? 'bg-green-50' : 'bg-red-50'
              }`}>
              {connectionStatus === 'success' ? (
                <CheckCircle className="h-5 w-5 text-green-600" />
              ) : (
                <XCircle className="h-5 w-5 text-red-600" />
              )}
              <div>
                <p className={`font-medium ${connectionStatus === 'success' ? 'text-green-800' : 'text-red-800'}`}>
                  {connectionStatus === 'success' ? 'Connected' : 'Connection Failed'}
                </p>
                {connectionInfo && connectionStatus === 'success' && (
                  <p className="text-sm text-green-600">
                    {connectionInfo.verifiedName} ({connectionInfo.phoneNumber})
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Business Account ID */}
          <div className="space-y-2">
            <Label htmlFor="business_account_id">Business Account ID *</Label>
            <Input
              id="business_account_id"
              placeholder="e.g., 123456789012345"
              value={formData.business_account_id}
              onChange={(e) => setFormData({ ...formData, business_account_id: e.target.value })}
            />
            <p className="text-xs text-gray-500">
              Found in Meta Business Suite under WhatsApp Manager
            </p>
          </div>

          {/* Phone Number ID */}
          <div className="space-y-2">
            <Label htmlFor="phone_number_id">Phone Number ID *</Label>
            <Input
              id="phone_number_id"
              placeholder="e.g., 123456789012345"
              value={formData.phone_number_id}
              onChange={(e) => setFormData({ ...formData, phone_number_id: e.target.value })}
            />
            <p className="text-xs text-gray-500">
              The ID of your WhatsApp Business phone number
            </p>
          </div>

          {/* Access Token */}
          <div className="space-y-2">
            <Label htmlFor="access_token">Access Token {hasExistingToken ? '(leave blank to keep existing)' : '*'}</Label>
            <div className="relative">
              <Input
                id="access_token"
                type={showToken ? 'text' : 'password'}
                placeholder={hasExistingToken ? '(unchanged — enter new token to update)' : 'Enter your permanent access token'}
                value={formData.access_token}
                onChange={(e) => setFormData({ ...formData, access_token: e.target.value })}
                className="pr-10"
              />
              <button
                type="button"
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                onClick={() => setShowToken(!showToken)}
              >
                {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            <p className="text-xs text-gray-500">
              {hasExistingToken ? 'A token is already saved. Only enter a new one if you want to update it.' : 'Generate a permanent token in Meta Developer Portal'}
            </p>
          </div>

          {/* Webhook Verify Token */}
          <div className="space-y-2">
            <Label htmlFor="webhook_verify_token">Webhook Verify Token (Optional)</Label>
            <Input
              id="webhook_verify_token"
              placeholder="Custom token for webhook verification"
              value={formData.webhook_verify_token}
              onChange={(e) => setFormData({ ...formData, webhook_verify_token: e.target.value })}
            />
            <p className="text-xs text-gray-500">
              Used to verify webhook requests from WhatsApp (for chatbot feature)
            </p>
          </div>

          {/* Actions */}
          <div className="flex space-x-3 pt-4">
            <Button onClick={handleSave} disabled={saving} className="flex-1">
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Save Settings
            </Button>
            <Button variant="outline" onClick={handleTestConnection} disabled={testing}>
              {testing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Test Connection
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Help Card */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Need Help?</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <h4 className="font-medium">How to get your credentials:</h4>
            <ol className="list-decimal list-inside text-sm text-gray-600 space-y-1">
              <li>Go to Meta Business Suite and create a WhatsApp Business account</li>
              <li>Navigate to WhatsApp Manager to find your Business Account ID</li>
              <li>Add a phone number and note the Phone Number ID</li>
              <li>Create a System User in Business Settings and generate a permanent token</li>
            </ol>
          </div>
          <a
            href="https://developers.facebook.com/docs/whatsapp/cloud-api/get-started"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center text-sm text-green-600 hover:underline"
          >
            View WhatsApp Cloud API Documentation
            <ExternalLink className="ml-1 h-3 w-3" />
          </a>
        </CardContent>
      </Card>
    </div>
  )
}
