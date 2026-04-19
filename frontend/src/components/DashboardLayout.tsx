'use client'

import { useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import Link from 'next/link'
import { signOut } from 'firebase/auth'
import { auth } from '@/lib/firebase'
import {
  MessageSquare,
  Settings,
  Menu,
  X,
  Home,
  LogOut,
  Zap,
  Users,
  Bot
} from 'lucide-react'

const navItems = [
  { href: '/dashboard', label: 'Dashboard', icon: Home },
  { href: '/dashboard/file-forward', label: 'Live Messaging', icon: Zap },
  { href: '/dashboard/bulk-message', label: 'Bulk Message', icon: Users },
  { href: '/dashboard/chatbot', label: 'Chatbot', icon: Bot },
  { href: '/dashboard/settings', label: 'Settings', icon: Settings },
]

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const handleLogout = async () => {
    try {
      await signOut(auth)
      router.push('/login')
    } catch (error) {
      console.error('Logout failed:', error)
    }
  }

  return (
    <div className="min-h-screen bg-black">
      {/* Mobile sidebar backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/80 z-40 lg:hidden backdrop-blur-sm"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={`
        fixed top-0 left-0 z-50 h-full w-64 bg-black border-r border-white/[0.06]
        lg:translate-x-0 transition-transform duration-300 ease-in-out
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
      `}>
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="flex items-center justify-between h-14 px-4 border-b border-white/[0.06]">
            <Link href="/dashboard" className="flex items-center gap-2.5">
              <div className="w-8 h-8 bg-[#25D366]/10 rounded-lg flex items-center justify-center">
                <MessageSquare className="h-4 w-4 text-[#25D366]" />
              </div>
              <span className="text-[15px] font-semibold text-white tracking-tight">WappFlow</span>
            </Link>
            <button
              className="lg:hidden p-2 hover:bg-white/[0.06] rounded-lg transition-apple"
              onClick={() => setSidebarOpen(false)}
            >
              <X className="h-5 w-5 text-[rgba(255,255,255,0.55)]" />
            </button>
          </div>

          {/* Navigation */}
          <nav className="flex-1 p-3 space-y-0.5 overflow-y-auto">
            {navItems.map((item) => {
              const Icon = item.icon
              const isActive = pathname === item.href
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`
                    flex items-center gap-3 py-2 pr-3 transition-colors duration-150
                    ${isActive
                      ? 'bg-[rgba(37,211,102,0.10)] text-white border-l-[2.5px] border-[#25D366] rounded-l-none rounded-r-[8px] pl-[18px] font-medium'
                      : 'text-[rgba(255,255,255,0.62)] hover:text-white hover:bg-[rgba(255,255,255,0.05)] pl-[20px] font-normal'
                    }
                  `}
                  onClick={() => setSidebarOpen(false)}
                >
                  <Icon className="h-4 w-4 shrink-0 text-current" />
                  <span className="text-[13px]">{item.label}</span>
                </Link>
              )
            })}
          </nav>

          {/* App info & logout section */}
          <div className="p-3 border-t border-white/[0.06] space-y-1">
            <div className="px-3 py-3 rounded-xl bg-white/[0.02] border border-white/[0.04]">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-8 h-8 rounded-full bg-[rgba(37,211,102,0.15)] border-[0.5px] border-[rgba(37,211,102,0.30)] flex items-center justify-center text-[#25D366] text-[12px] font-semibold">
                  U
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] font-medium text-white truncate">User</p>
                  <p className="text-[11px] text-[rgba(255,255,255,0.45)]">Admin</p>
                </div>
              </div>
              <button
                onClick={handleLogout}
                className="flex items-center gap-2.5 w-full px-2.5 py-2 rounded-lg text-[rgba(255,255,255,0.50)] hover:text-white hover:bg-[rgba(255,255,255,0.05)] transition-colors duration-150"
              >
                <LogOut className="h-4 w-4 text-current" />
                <span className="text-[13px] font-normal">Logout</span>
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* Mobile menu trigger */}
      {!sidebarOpen && (
        <button
          className="fixed top-3 left-3 z-30 lg:hidden p-2 hover:bg-white/[0.06] rounded-lg transition-apple"
          onClick={() => setSidebarOpen(true)}
          aria-label="Open sidebar"
        >
          <Menu className="h-5 w-5 text-[rgba(255,255,255,0.55)]" />
        </button>
      )}

      {/* Main content */}
      <div className="lg:pl-64 pt-10">
        {children}
      </div>
    </div>
  )
}
