'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { MessageSquare } from 'lucide-react'

export default function Home() {
  const router = useRouter()
  const { user, loading } = useAuth()

  useEffect(() => {
    if (!loading) {
      if (user) {
        router.push('/dashboard')
      } else {
        router.push('/login')
      }
    }
  }, [user, loading, router])

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
