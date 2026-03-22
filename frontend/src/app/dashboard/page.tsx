'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { settings } from '@/lib/api'
import { Skeleton } from '@/components/ui/skeleton'
import { MessageSquare, Users, Bot, ArrowRight, CheckCircle, AlertCircle } from 'lucide-react'

interface UsageStats {
  today: { total: number; successful: number; failed: number }
  month: { total: number; successful: number; failed: number }
  byProduct: { product_type: string; total: number }[]
}

const products = [
  {
    id: 'file-forward',
    title: 'Live Messaging',
    description: 'Send real-time messages, images, or documents within the 24-hour WhatsApp session window.',
    icon: MessageSquare,
    href: '/dashboard/file-forward',
    color: 'bg-blue-500',
    lightColor: 'bg-blue-50',
    textColor: 'text-blue-600'
  },
  {
    id: 'bulk-message',
    title: 'Bulk WhatsApp Messaging',
    description: 'Send messages to multiple contacts using Excel/CSV. Ideal for marketing campaigns and announcements.',
    icon: Users,
    href: '/dashboard/bulk-message',
    color: 'bg-purple-500',
    lightColor: 'bg-purple-50',
    textColor: 'text-purple-600'
  },
  {
    id: 'chatbot',
    title: 'Auto-Reply Chatbot',
    description: 'Automatically respond to incoming messages with keyword-based rules. Great for FAQs and support.',
    icon: Bot,
    href: '/dashboard/chatbot',
    color: 'bg-orange-500',
    lightColor: 'bg-orange-50',
    textColor: 'text-orange-600'
  }
]

export default function DashboardPage() {
  const [usage, setUsage] = useState<UsageStats | null>(null)
  const [isConfigured, setIsConfigured] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [usageRes, settingsRes] = await Promise.all([
          settings.getUsage(),
          settings.getWhatsApp()
        ])
        setUsage(usageRes.data)
        setIsConfigured(settingsRes.data.settings?.is_configured || false)
      } catch (error) {
        console.error('Failed to fetch dashboard data:', error)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-500 mt-1">Choose a WhatsApp automation service to get started</p>
      </div>

      {/* Configuration Status */}
      {loading ? (
        <Skeleton className="h-16 w-full rounded-lg" />
      ) : isConfigured === false ? (
        <Card className="border-yellow-200 bg-yellow-50">
          <CardContent className="flex items-center justify-between py-4">
            <div className="flex items-center space-x-3">
              <AlertCircle className="h-5 w-5 text-yellow-600" />
              <div>
                <p className="font-medium text-yellow-800">WhatsApp not configured</p>
                <p className="text-sm text-yellow-600">Configure your WhatsApp Business API to start sending messages</p>
              </div>
            </div>
            <Link href="/dashboard/settings">
              <Button variant="outline" className="border-yellow-400 text-yellow-700 hover:bg-yellow-100">
                Configure Now
              </Button>
            </Link>
          </CardContent>
        </Card>
      ) : isConfigured === true ? (
        <Card className="border-green-200 bg-green-50">
          <CardContent className="flex items-center space-x-3 py-4">
            <CheckCircle className="h-5 w-5 text-green-600" />
            <p className="font-medium text-green-800">WhatsApp Business API connected and ready</p>
          </CardContent>
        </Card>
      ) : null}

      {/* Product Cards */}
      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {loading
          ? Array.from({ length: 3 }).map((_, i) => (
              <Card key={i}>
                <CardHeader>
                  <Skeleton className="w-12 h-12 rounded-lg mb-3" />
                  <Skeleton className="h-5 w-40" />
                  <Skeleton className="h-4 w-full mt-2" />
                  <Skeleton className="h-4 w-3/4 mt-1" />
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-10 w-full rounded-md" />
                </CardContent>
              </Card>
            ))
          : products.map((product) => {
              const Icon = product.icon
              return (
                <Card key={product.id} className="hover:shadow-lg transition-shadow duration-200">
                  <CardHeader>
                    <div className={`w-12 h-12 ${product.lightColor} rounded-lg flex items-center justify-center mb-3`}>
                      <Icon className={`h-6 w-6 ${product.textColor}`} />
                    </div>
                    <CardTitle className="text-lg">{product.title}</CardTitle>
                    <CardDescription>{product.description}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <Link href={product.href}>
                      <Button className="w-full group">
                        Get Started
                        <ArrowRight className="ml-2 h-4 w-4 group-hover:translate-x-1 transition-transform" />
                      </Button>
                    </Link>
                  </CardContent>
                </Card>
              )
            })}
      </div>

      {/* Usage Stats */}
      {loading ? (
        <div className="grid gap-6 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-9 w-16 mt-2" />
              </CardHeader>
              <CardContent>
                <div className="flex space-x-4">
                  <Skeleton className="h-4 w-16" />
                  <Skeleton className="h-4 w-16" />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : usage ? (
        <div className="grid gap-6 md:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Today&apos;s Messages</CardDescription>
              <CardTitle className="text-3xl">{usage.today.total}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex space-x-4 text-sm">
                <span className="text-green-600">{usage.today.successful} sent</span>
                <span className="text-red-600">{usage.today.failed} failed</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardDescription>This Month</CardDescription>
              <CardTitle className="text-3xl">{usage.month.total}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex space-x-4 text-sm">
                <span className="text-green-600">{usage.month.successful} sent</span>
                <span className="text-red-600">{usage.month.failed} failed</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardDescription>By Product (30 days)</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {usage.byProduct.length > 0 ? (
                  usage.byProduct.map((item) => (
                    <div key={item.product_type} className="flex justify-between text-sm">
                      <span className="capitalize">{item.product_type.replace('_', ' ')}</span>
                      <span className="font-medium">{item.total}</span>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-gray-500">No messages sent yet</p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </div>
  )
}
