'use client'

import { useEffect, useState } from 'react'
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
  Bot,
  Sun,
  Moon
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
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

  useEffect(() => {
    const storedTheme = localStorage.getItem('theme')
    const nextTheme = storedTheme === 'light' ? 'light' : 'dark'
    setTheme(nextTheme)
    document.documentElement.setAttribute('data-theme', nextTheme)
  }, [])

  const handleThemeToggle = () => {
    const nextTheme = theme === 'dark' ? 'light' : 'dark'
    setTheme(nextTheme)
    localStorage.setItem('theme', nextTheme)
    document.documentElement.setAttribute('data-theme', nextTheme)
  }

  const handleLogout = async () => {
    try {
      await signOut(auth)
      router.push('/login')
    } catch (error) {
      console.error('Logout failed:', error)
    }
  }

  return (
    <div className="min-h-screen bg-page">
      {/* Mobile sidebar backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/80 z-40 lg:hidden backdrop-blur-sm"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={`
        fixed top-0 left-0 z-50 h-full w-[220px] bg-page border-r border-[var(--border-subtle)]
        lg:translate-x-0 transition-transform duration-300 ease-in-out
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
      `}>
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="flex items-center justify-between h-14 px-4 border-b border-[var(--border-subtle)]">
            <Link href="/dashboard" className="flex items-center gap-2.5">
              <div className="w-8 h-8 bg-[var(--accent-dim)] rounded-[var(--radius-md)] flex items-center justify-center">
                <MessageSquare className="h-4 w-4 text-[var(--accent)]" />
              </div>
              <span className="text-[15px] font-semibold text-primary tracking-[-0.03em]">WappFlow</span>
            </Link>
            <button
              className="lg:hidden p-2 hover:bg-[var(--bg-hover)] rounded-[var(--radius-md)] transition-apple"
              onClick={() => setSidebarOpen(false)}
            >
              <X className="h-5 w-5 text-secondary" />
            </button>
          </div>

          <div className="px-4 py-2.5 border-b border-[var(--border-subtle)]">
            <button
              onClick={handleThemeToggle}
              className={`relative h-[20px] w-[36px] rounded-[980px] border border-[var(--border-default)] transition-all duration-300 [transition-timing-function:cubic-bezier(0.4,0,0.2,1)] ${theme === 'dark' ? 'bg-[#1c1c1e]' : 'bg-[#e5e5ea]'}`}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            >
              <span
                className={`absolute top-[1px] h-4 w-4 rounded-full flex items-center justify-center transition-all duration-300 [transition-timing-function:cubic-bezier(0.4,0,0.2,1)] ${theme === 'dark' ? 'left-[1px] bg-[#25D366]' : 'left-[19px] bg-white shadow-[0_1px_3px_rgba(0,0,0,0.2)]'}`}
              >
                {theme === 'dark' ? (
                  <Moon className="h-2.5 w-2.5 text-[rgba(255,255,255,0.80)]" />
                ) : (
                  <Sun className="h-2.5 w-2.5 text-[#f59e0b]" />
                )}
              </span>
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
                      ? 'bg-[rgba(37,211,102,0.07)] text-primary border-l-2 border-[#25D366] rounded-r-[8px] pl-[18px] font-medium'
                      : 'text-[var(--nav-inactive)] font-normal hover:text-primary hover:bg-[var(--bg-hover)] pl-[20px]'
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
          <div className="p-3 border-t border-[var(--border-subtle)] space-y-1">
            <div className="px-3 py-3 rounded-[var(--radius-lg)] bg-[var(--bg-surface)] border border-[var(--border-subtle)]">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-8 h-8 rounded-full bg-[rgba(37,211,102,0.12)] border-[0.5px] border-[rgba(37,211,102,0.30)] flex items-center justify-center text-[var(--accent)] text-[12px] font-semibold">
                  U
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] font-medium text-primary truncate">User</p>
                  <p className="text-[11px] text-tertiary">Admin</p>
                </div>
              </div>
              <button
                onClick={handleLogout}
                className="flex items-center gap-2.5 w-full px-2.5 py-2 rounded-[var(--radius-md)] text-secondary hover:text-primary hover:bg-[var(--bg-hover)] transition-colors duration-150"
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
          className="fixed top-3 left-3 z-30 lg:hidden p-2 hover:bg-[var(--bg-hover)] rounded-[var(--radius-md)] transition-apple"
          onClick={() => setSidebarOpen(true)}
          aria-label="Open sidebar"
        >
          <Menu className="h-5 w-5 text-secondary" />
        </button>
      )}

      {/* Main content */}
      <div className="lg:pl-[220px] pt-10">
        {children}
      </div>
    </div>
  )
}
