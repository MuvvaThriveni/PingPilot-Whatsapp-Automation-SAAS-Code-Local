'use client'

import { useState, useEffect } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { chatbot } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { Bot, Save, MessageCircle, Loader2, RefreshCw } from 'lucide-react'

interface ChatbotSettings {
  is_enabled: number
  fallback_message: string
  use_ai?: boolean
  ai_system_prompt?: string
  openai_api_key?: string
}

interface Conversation {
  sender_phone: string
  sender_name: string
  message_text: string
  direction: string
  created_at: string
}

export default function ChatbotPage() {
  const { toast } = useToast()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [settings, setSettings] = useState<ChatbotSettings>({ 
    is_enabled: 0, 
    fallback_message: '',
    use_ai: true,
    ai_system_prompt: '',
    openai_api_key: ''
  })
  const [conversations, setConversations] = useState<Conversation[]>([])

  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      const [settingsRes, convRes] = await Promise.all([
        chatbot.getSettings(),
        chatbot.getConversations()
      ])
      setSettings(settingsRes.data.settings)
      setConversations(convRes.data.conversations)
    } catch (error) {
      console.error('Failed to fetch chatbot data:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await fetchData()
    } finally {
      setRefreshing(false)
    }
  }

  const handleToggle = async (enabled: boolean) => {
    try {
      await chatbot.updateSettings({ 
        is_enabled: enabled, 
        fallback_message: settings.fallback_message,
        use_ai: settings.use_ai,
        ai_system_prompt: settings.ai_system_prompt,
        openai_api_key: settings.openai_api_key
      })
      setSettings({ ...settings, is_enabled: enabled ? 1 : 0 })
      toast({ title: enabled ? 'Chatbot enabled' : 'Chatbot disabled' })
    } catch (error) {
      toast({ title: 'Error', description: 'Failed to update settings', variant: 'destructive' })
    }
  }

  const handleSaveSettings = async () => {
    setSaving(true)
    try {
      await chatbot.updateSettings({
        is_enabled: settings.is_enabled === 1,
        fallback_message: settings.fallback_message,
        use_ai: settings.use_ai,
        ai_system_prompt: settings.ai_system_prompt,
        openai_api_key: settings.openai_api_key
      })
      toast({ title: 'Settings saved' })
    } catch (error) {
      toast({ title: 'Error', description: 'Failed to save settings', variant: 'destructive' })
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">AI Auto-Reply Chatbot</h1>
        <p className="text-gray-500 mt-1">Automatically respond to incoming WhatsApp messages using ChatGPT</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Settings */}
        <Card className="lg:col-span-1">
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle>Chatbot Settings</CardTitle>
                <CardDescription>Configure auto-reply behavior</CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRefresh}
                  disabled={refreshing}
                >
                  <RefreshCw className={`h-4 w-4 mr-1 ${refreshing ? 'animate-spin' : ''}`} />
                  Refresh
                </Button>
                <Switch
                  checked={settings.is_enabled === 1}
                  onCheckedChange={handleToggle}
                />
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center space-x-2 p-3 rounded-lg bg-gray-50">
              <Bot className={`h-5 w-5 ${settings.is_enabled ? 'text-green-500' : 'text-gray-400'}`} />
              <span className={settings.is_enabled ? 'text-green-700' : 'text-gray-500'}>
                {settings.is_enabled ? 'Chatbot is active' : 'Chatbot is disabled'}
              </span>
            </div>

            {/* OpenAI API Key */}
            <div className="space-y-2">
              <Label htmlFor="apiKey">OpenAI API Key</Label>
              <Input
                id="apiKey"
                type="password"
                placeholder="sk-..."
                value={settings.openai_api_key || ''}
                onChange={(e) => setSettings({ ...settings, openai_api_key: e.target.value })}
              />
              <p className="text-xs text-gray-500">
                Get your API key from <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">OpenAI Dashboard</a>
              </p>
            </div>

            {/* AI System Prompt */}
            <div className="space-y-2">
              <Label htmlFor="systemPrompt">AI System Prompt</Label>
              <Textarea
                id="systemPrompt"
                placeholder="You are a helpful customer service assistant..."
                value={settings.ai_system_prompt || ''}
                onChange={(e) => setSettings({ ...settings, ai_system_prompt: e.target.value })}
                rows={4}
              />
              <p className="text-xs text-gray-500">
                Instructions for how the AI should behave and respond
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="fallback">Fallback Message</Label>
              <Textarea
                id="fallback"
                placeholder="Message to send when AI is unavailable..."
                value={settings.fallback_message}
                onChange={(e) => setSettings({ ...settings, fallback_message: e.target.value })}
                rows={3}
              />
              <p className="text-xs text-gray-500">
                Sent when AI fails or API key is not configured
              </p>
            </div>

            <Button onClick={handleSaveSettings} disabled={saving} className="w-full">
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
              Save Settings
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Recent Conversations */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Conversations</CardTitle>
          <CardDescription>View recent chatbot interactions</CardDescription>
        </CardHeader>
        <CardContent>
          {conversations.length > 0 ? (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {conversations.map((conv, index) => (
                <div
                  key={index}
                  className={`p-3 rounded-lg ${
                    conv.direction === 'incoming' ? 'bg-gray-100' : 'bg-green-50 ml-8'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium">
                      {conv.direction === 'incoming' ? conv.sender_name || conv.sender_phone : 'Bot'}
                    </span>
                    <span className="text-xs text-gray-400">
                      {new Date(conv.created_at).toLocaleString()}
                    </span>
                  </div>
                  <p className="text-sm">{conv.message_text}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-gray-500">
              <MessageCircle className="h-12 w-12 mx-auto mb-3 text-gray-300" />
              <p>No conversations yet</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
