'use client'

import { useAuth } from '@/contexts/AuthContext'
import { useRouter } from 'next/navigation'
import { useEffect } from 'react'
import DashboardLayout from '@/components/DashboardLayout'
import { MessageSquare } from 'lucide-react'

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (!loading && !user) {
      router.push('/login')
    }
  }, [user, loading, router])

  if (loading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="inline-flex items-center gap-2.5 mb-4">
            <div className="w-10 h-10 bg-[#25D366]/10 rounded-lg flex items-center justify-center">
              <MessageSquare className="w-5 h-5 text-[#25D366]" />
            </div>
            <span className="text-lg font-semibold text-white tracking-tight">WappFlow</span>
          </div>
          <div className="w-5 h-5 border-2 border-[#25D366]/30 border-t-[#25D366] rounded-full animate-spin mx-auto" />
        </div>
      </div>
    )
  }

  if (!user) {
    return null
  }

  return <DashboardLayout>{children}</DashboardLayout>
}
