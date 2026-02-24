'use client'

import { useState, useEffect, useRef } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { chatbot } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { Bot, Save, MessageCircle, Loader2, RefreshCw, User, ChevronLeft } from 'lucide-react'

interface ChatbotSettings {
  is_enabled: number
  fallback_message: string
  use_ai?: boolean
  ai_system_prompt?: string
  openai_api_key?: string
}

interface ChatUser {
  phone: string
  name: string
  last_message: string
  last_message_at: string
  direction: string
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
  const [users, setUsers] = useState<ChatUser[]>([])
  const [selectedUser, setSelectedUser] = useState<ChatUser | null>(null)
  const [userConversations, setUserConversations] = useState<Conversation[]>([])
  const [loadingConversations, setLoadingConversations] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetchData()
    // Auto-refresh every 30 seconds (reduced from 5s to cut Firestore quota)
    const interval = setInterval(() => {
      fetchUsersQuietly()
      if (selectedUser) {
        fetchUserConversationsQuietly(selectedUser.phone)
      }
    }, 30000)
    return () => clearInterval(interval)
  }, [selectedUser])

  const fetchData = async () => {
    try {
      const [settingsRes, usersRes] = await Promise.all([
        chatbot.getSettings(),
        chatbot.getUsers()
      ])
      setSettings(settingsRes.data.settings)
      setUsers(usersRes.data.users)
    } catch (error) {
      console.error('Failed to fetch chatbot data:', error)
    } finally {
      setLoading(false)
    }
  }

  const fetchUsersQuietly = async () => {
    try {
      const res = await chatbot.getUsers()
      setUsers(res.data.users)
    } catch (error) {
      console.error('Failed to refresh users:', error)
    }
  }

  const fetchUserConversationsQuietly = async (phone: string) => {
    try {
      const res = await chatbot.getUserConversations(phone)
      setUserConversations(res.data.conversations)
    } catch (error) {
      console.error('Failed to refresh conversations:', error)
    }
  }

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await fetchData()
      if (selectedUser) {
        await fetchUserConversations(selectedUser.phone)
      }
    } finally {
      setRefreshing(false)
    }
  }

  const fetchUserConversations = async (phone: string) => {
    setLoadingConversations(true)
    try {
      const res = await chatbot.getUserConversations(phone)
      setUserConversations(res.data.conversations)
      setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 100)
    } catch (error) {
      console.error('Failed to fetch user conversations:', error)
    } finally {
      setLoadingConversations(false)
    }
  }

  const handleSelectUser = async (user: ChatUser) => {
    setSelectedUser(user)
    await fetchUserConversations(user.phone)
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
        <h1 className="text-2xl font-bold text-gray-900">WhatsApp Chatbot Trigger</h1>
        <p className="text-gray-500 mt-1">Configure auto-trigger rules and fallback messages</p>
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

            {/* OpenAI API Key (Disabled)
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
            */}

            {/* AI System Prompt (Disabled)
            <div className="space-y-2">
              <Label htmlFor="systemPrompt">AI System Prompt</Label>
              <Textarea
                id="systemPrompt"
                placeholder="You are a helpful customer service assistant..."
                value={settings.ai_system_prompt || ''}
                onChange={(e) => setSettings({ ...settings, ai_system_prompt: e.target.value })}
                rows={4}
                maxLength={1500}
              />
              <div className="flex justify-between">
                <p className="text-xs text-gray-500">
                  Instructions for how the AI should behave and respond
                </p>
                <p className="text-xs text-gray-500">
                  {(settings.ai_system_prompt || '').length}/1500
                </p>
              </div>
            </div>
            */}

            {/* Fallback Message (Disabled - replaced by first_trigger template)
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
            */}

            <Button onClick={handleSaveSettings} disabled={saving} className="w-full">
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
              Save Settings
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Chat Section */}
      <Card>
        <CardHeader>
          <CardTitle>Conversations</CardTitle>
          <CardDescription>Click on a user to view their chat history</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex gap-4 h-[500px]">
            {/* Users List */}
            <div className="w-1/3 border-r pr-4 overflow-y-auto">
              {users.length > 0 ? (
                <div className="space-y-2">
                  {users.map((user) => (
                    <div
                      key={user.phone}
                      onClick={() => handleSelectUser(user)}
                      className={`p-3 rounded-lg cursor-pointer transition-colors ${selectedUser?.phone === user.phone
                        ? 'bg-blue-100 border-blue-300 border'
                        : 'bg-gray-50 hover:bg-gray-100'
                        }`}
                    >
                      <div className="flex items-center gap-2">
                        <User className="h-8 w-8 p-1.5 bg-gray-200 rounded-full text-gray-600" />
                        <div className="flex-1 min-w-0">
                          <p className="font-medium text-sm truncate">
                            {user.name || user.phone}
                          </p>
                          <p className="text-xs text-gray-500 truncate">
                            {user.last_message}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-gray-500">
                  <User className="h-10 w-10 mx-auto mb-2 text-gray-300" />
                  <p className="text-sm">No users yet</p>
                </div>
              )}
            </div>

            {/* Chat History */}
            <div className="w-2/3 flex flex-col">
              {selectedUser ? (
                <>
                  {/* Chat Header */}
                  <div className="flex items-center gap-2 pb-3 border-b mb-3">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="lg:hidden"
                      onClick={() => setSelectedUser(null)}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <User className="h-8 w-8 p-1.5 bg-blue-100 rounded-full text-blue-600" />
                    <div>
                      <p className="font-medium">{selectedUser.name || selectedUser.phone}</p>
                      <p className="text-xs text-gray-500">{selectedUser.phone}</p>
                    </div>
                  </div>

                  {/* Messages */}
                  <div className="flex-1 overflow-y-auto space-y-2 pr-2">
                    {loadingConversations ? (
                      <div className="flex items-center justify-center h-full">
                        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
                      </div>
                    ) : userConversations.length > 0 ? (
                      <>
                        {userConversations.map((conv, index) => (
                          <div
                            key={index}
                            className={`p-3 rounded-lg max-w-[85%] ${conv.direction === 'incoming'
                              ? 'bg-gray-100'
                              : 'bg-green-100 ml-auto'
                              }`}
                          >
                            <p className="text-sm">{conv.message_text}</p>
                            <p className="text-xs text-gray-400 mt-1">
                              {new Date(conv.created_at).toLocaleTimeString()}
                            </p>
                          </div>
                        ))}
                        <div ref={chatEndRef} />
                      </>
                    ) : (
                      <div className="text-center py-8 text-gray-500">
                        <p className="text-sm">No messages</p>
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className="flex items-center justify-center h-full text-gray-500">
                  <div className="text-center">
                    <MessageCircle className="h-12 w-12 mx-auto mb-3 text-gray-300" />
                    <p>Select a user to view chat history</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
