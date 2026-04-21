'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { settings } from '@/lib/api'
import { Skeleton } from '@/components/ui/skeleton'
import {
  MessageSquare,
  Users,
  Bot,
  ArrowRight,
  CheckCircle,
  AlertCircle,
  TrendingUp
} from 'lucide-react'

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
    accentColor: '#25D366',
  },
  {
    id: 'bulk-message',
    title: 'Bulk WhatsApp Messaging',
    description: 'Send messages to multiple contacts using Excel/CSV. Ideal for marketing campaigns and announcements.',
    icon: Users,
    href: '/dashboard/bulk-message',
    accentColor: '#a855f7',
  },
  {
    id: 'chatbot',
    title: 'Auto-Reply Chatbot',
    description: 'Automatically respond to incoming messages with keyword-based rules. Great for FAQs and support.',
    icon: Bot,
    href: '/dashboard/chatbot',
    accentColor: '#f97316',
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
    <div className="min-h-screen bg-page">
      {/* Hero Section */}
      <section className="relative px-6 py-16 md:py-24">
        <div className="max-w-4xl mx-auto text-center space-y-6">
          {/* Eyebrow */}
          <p className="text-eyebrow">
            WhatsApp Automation Platform
          </p>

          {/* Headline */}
          <h1 className="text-hero">
            Automate your WhatsApp<br />
            communications at scale
          </h1>

          {/* Subtitle */}
          <p className="text-body max-w-2xl mx-auto text-[15px]">
            Send bulk messages, set up auto-replies, and manage customer conversations
            with an enterprise-grade WhatsApp Business API integration.
          </p>

          {/* CTA Buttons */}
          <div className="flex items-center justify-center gap-3 pt-4">
            <Link href="/dashboard/file-forward">
              <button className="btn-pill px-6 py-2.5 bg-[var(--accent)] text-[var(--accent-contrast)] text-sm font-semibold hover:scale-[1.01] transition-apple active:scale-[0.99]">
                Start Messaging
              </button>
            </Link>
            <Link href="/dashboard/settings">
              <button className="btn-pill px-6 py-2.5 bg-[var(--bg-elevated)] text-secondary text-sm font-semibold border-[0.5px] border-[var(--border-default)] hover:text-primary hover:scale-[1.01] transition-apple active:scale-[0.99]">
                Configure API
              </button>
            </Link>
          </div>
        </div>
      </section>

      {/* Stats Bar */}
      <section className="px-6 pb-8">
        <div className="max-w-6xl mx-auto">
          <div className="bg-[var(--bg-card)] rounded-[var(--radius-xl)] border-[0.5px] border-[var(--border-default)] overflow-hidden">
            <div className="grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-[var(--border-subtle)]">
              {/* Today's Messages */}
              <div className="p-6 text-center">
                {loading ? (
                  <div className="space-y-3">
                    <Skeleton className="h-10 w-20 mx-auto rounded-lg" />
                    <Skeleton className="h-4 w-28 mx-auto" />
                  </div>
                ) : (
                  <>
                    <div className="text-stat-number">
                      {usage?.today.total ?? 0}
                    </div>
                    <div className="text-label mt-1">
                      Today&apos;s Messages
                    </div>
                    <div className="flex items-center justify-center gap-1 mt-2">
                      <span className="text-[12px] text-[#25D366]">
                        +{usage?.today.successful ?? 0} sent
                      </span>
                    </div>
                  </>
                )}
              </div>

              {/* This Month */}
              <div className="p-6 text-center">
                {loading ? (
                  <div className="space-y-3">
                    <Skeleton className="h-10 w-20 mx-auto rounded-lg" />
                    <Skeleton className="h-4 w-28 mx-auto" />
                  </div>
                ) : (
                  <>
                    <div className="text-stat-number">
                      {usage?.month.total ?? 0}
                    </div>
                    <div className="text-label mt-1">
                      This Month
                    </div>
                    <div className="flex items-center justify-center gap-1 mt-2">
                      <span className="text-[12px] text-[#25D366]">
                        +{usage?.month.successful ?? 0} sent
                      </span>
                    </div>
                  </>
                )}
              </div>

              {/* Success Rate */}
              <div className="p-6 text-center">
                {loading ? (
                  <div className="space-y-3">
                    <Skeleton className="h-10 w-20 mx-auto rounded-lg" />
                    <Skeleton className="h-4 w-28 mx-auto" />
                  </div>
                ) : (
                  <>
                    <div className="text-stat-number">
                      {usage?.month.total && usage?.month.successful
                        ? Math.round((usage.month.successful / usage.month.total) * 100)
                        : 100}%
                    </div>
                    <div className="text-label mt-1">
                      Success Rate
                    </div>
                    <div className="flex items-center justify-center gap-1 mt-2">
                      <TrendingUp className="w-3.5 h-3.5 text-[#25D366]" />
                      <span className="text-[12px] text-[#25D366]">
                        {usage?.month.failed ?? 0} failed
                      </span>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Configuration Status */}
      {loading ? (
        <section className="px-6 pb-8">
          <div className="max-w-6xl mx-auto">
            <Skeleton className="h-14 w-full rounded-xl" />
          </div>
        </section>
      ) : isConfigured === false ? (
        <section className="px-6 pb-8">
          <div className="max-w-6xl mx-auto">
            <div className="bg-[var(--bg-elevated)] rounded-[var(--radius-lg)] border-[0.5px] border-[var(--border-default)] p-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <AlertCircle className="w-5 h-5 text-[#f97316]" />
                <div>
                  <p className="text-[14px] font-semibold text-primary">WhatsApp not configured</p>
                  <p className="text-[13px] text-secondary">Configure your WhatsApp Business API to start sending messages</p>
                </div>
              </div>
              <Link href="/dashboard/settings">
                <button className="btn-pill px-4 py-2 text-[13px] font-semibold bg-[var(--bg-card)] border-[0.5px] border-[var(--border-default)] text-secondary hover:text-primary hover:scale-[1.01] transition-apple">
                  Configure Now
                </button>
              </Link>
            </div>
          </div>
        </section>
      ) : isConfigured === true ? (
        <section className="px-6 pb-8">
          <div className="max-w-6xl mx-auto">
            <div className="bg-[var(--bg-surface)] rounded-[var(--radius-lg)] border-[0.5px] border-[var(--accent-border)] p-4 flex items-center gap-3">
              <CheckCircle className="w-5 h-5 text-[#25D366]" />
              <p className="text-[14px] font-semibold text-primary">WhatsApp Business API connected and ready</p>
            </div>
          </div>
        </section>
      ) : null}

      {/* Feature Cards */}
      <section className="px-6 pb-16">
        <div className="max-w-6xl mx-auto">
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {loading
              ? Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="bg-card-apple rounded-[var(--radius-xl)] border-[0.5px] border-[var(--border-subtle)] p-6">
                    <Skeleton className="w-11 h-11 rounded-xl mb-4" />
                    <Skeleton className="h-5 w-48 mb-2" />
                    <Skeleton className="h-4 w-full mb-1" />
                    <Skeleton className="h-4 w-3/4 mb-4" />
                    <Skeleton className="h-10 w-full rounded-xl" />
                  </div>
                ))
              : products.map((product) => {
                  const Icon = product.icon
                  return (
                    <Link key={product.id} href={product.href}>
                      <div className="group bg-card-apple rounded-[var(--radius-xl)] border-[0.5px] border-[var(--border-subtle)] p-6 hover:border-[var(--border-strong)] hover:bg-elevated transition-apple h-full">
                        {/* Icon */}
                        <div
                          className="w-11 h-11 rounded-[var(--radius-md)] flex items-center justify-center mb-4 bg-[var(--bg-elevated)] border-[0.5px] border-[var(--border-subtle)]"
                        >
                          <Icon
                            className="w-5 h-5"
                            style={{ color: product.accentColor }}
                          />
                        </div>

                        {/* Badge */}
                        <div className="flex items-center justify-between mb-3">
                          <h3 className="text-card-title">
                            {product.title}
                          </h3>
                        </div>

                        {/* Description */}
                        <p className="text-body text-[13px] leading-relaxed mb-5">
                          {product.description}
                        </p>

                        {/* CTA Link */}
                        <div className="flex items-center gap-2" style={{ color: product.accentColor }}>
                          <span className="text-[13px] font-semibold">Open</span>
                          <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 transition-apple" />
                        </div>
                      </div>
                    </Link>
                  )
                })}
          </div>
        </div>
      </section>
    </div>
  )
}
