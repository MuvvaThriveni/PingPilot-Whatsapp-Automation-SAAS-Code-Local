'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { signInWithEmailAndPassword } from 'firebase/auth'
import { auth } from '@/lib/firebase'
import { Mail, Lock, Eye, EyeOff, MessageSquare, ArrowRight, Sparkles, ShieldCheck } from 'lucide-react'

export default function LoginPage() {
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await signInWithEmailAndPassword(auth, email, password)
      router.push('/dashboard')
    } catch (err: unknown) {
      const firebaseError = err as { code?: string }
      if (firebaseError.code === 'auth/invalid-credential' || firebaseError.code === 'auth/wrong-password' || firebaseError.code === 'auth/user-not-found') {
        setError('Invalid email or password.')
      } else if (firebaseError.code === 'auth/too-many-requests') {
        setError('Too many failed attempts. Please try again later.')
      } else {
        setError('Login failed. Please check your credentials.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-screen bg-black overflow-hidden">
      <div className="pointer-events-none absolute -top-28 -left-24 h-80 w-80 rounded-full bg-[#25D366]/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -right-24 h-96 w-96 rounded-full bg-white/[0.06] blur-3xl" />

      <div className="relative mx-auto flex min-h-screen w-full max-w-6xl items-center px-6 py-10">
        <div className="grid w-full gap-8 lg:grid-cols-[1.1fr_0.9fr] lg:gap-12">
          {/* Left content */}
          <section className="hidden lg:flex flex-col justify-center">
            <div className="inline-flex w-fit items-center gap-2 rounded-full border border-white/[0.12] bg-white/[0.04] px-3 py-1.5 mb-6">
              <Sparkles className="h-3.5 w-3.5 text-[#25D366]" />
              <span className="text-[12px] text-[rgba(255,255,255,0.70)]">Secure WhatsApp Automation</span>
            </div>

            <h1 className="text-[44px] leading-[1.06] tracking-tight text-white font-bold max-w-[560px]">
              Sign in and start sending smarter at scale
            </h1>
            <p className="mt-5 max-w-[520px] text-[15px] leading-relaxed text-[rgba(255,255,255,0.52)]">
              Access Live Messaging, Bulk Campaigns, and Chatbot workflows from one dashboard built for fast operations.
            </p>

            <div className="mt-8 inline-flex items-center gap-2 text-[13px] text-[rgba(255,255,255,0.62)]">
              <ShieldCheck className="h-4 w-4 text-[#25D366]" />
              End-to-end authenticated access with Firebase
            </div>
          </section>

          {/* Form Card */}
          <section className="w-full max-w-[460px] lg:ml-auto">
            <div className="rounded-3xl border border-white/[0.10] bg-[#0c0c0c]/95 p-6 sm:p-8 shadow-[0_10px_60px_rgba(0,0,0,0.45)]">
              {/* Logo */}
              <div className="mb-8">
                <div className="inline-flex items-center gap-2.5 mb-6">
                  <div className="w-10 h-10 bg-[#25D366]/10 rounded-lg flex items-center justify-center">
                    <MessageSquare className="w-5 h-5 text-[#25D366]" />
                  </div>
                  <span className="text-lg font-semibold text-white tracking-tight">WappFlow</span>
                </div>
                <h2 className="text-[30px] font-bold text-white tracking-tight mb-2">
                  Welcome back
                </h2>
                <p className="text-[15px] text-[rgba(255,255,255,0.58)]">
                  Sign in to continue to your dashboard
                </p>
              </div>

              {error && (
                <div className="mb-6 p-3 bg-red-500/10 border border-red-500/20 rounded-xl">
                  <p className="text-[13px] text-red-400 font-medium">{error}</p>
                </div>
              )}

              <form onSubmit={handleLogin} className="space-y-5">
                {/* Email */}
                <div className="space-y-2">
                  <label className="text-[13px] font-medium text-[rgba(255,255,255,0.62)]">
                    Email address
                  </label>
                  <div className="relative">
                    <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-tertiary" />
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@example.com"
                      required
                      className="w-full bg-[#111111] pl-11 pr-4 py-3 rounded-xl border border-white/[0.07] placeholder:text-tertiary text-white text-[14px] focus:outline-none focus:ring-2 focus:ring-[#25D366]/20 focus:border-[#25D366]/30 transition-all"
                    />
                  </div>
                </div>

                {/* Password */}
                <div className="space-y-2">
                  <label className="text-[13px] font-medium text-[rgba(255,255,255,0.62)]">
                    Password
                  </label>
                  <div className="relative">
                    <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-tertiary" />
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="Enter your password"
                      required
                      className="w-full bg-[#111111] pl-11 pr-11 py-3 rounded-xl border border-white/[0.07] placeholder:text-tertiary text-white text-[14px] focus:outline-none focus:ring-2 focus:ring-[#25D366]/20 focus:border-[#25D366]/30 transition-all"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3.5 top-1/2 -translate-y-1/2 text-tertiary hover:text-secondary transition-colors"
                    >
                      {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {/* Submit Button */}
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full btn-pill h-11 bg-[#25D366] text-black font-semibold text-[14px] hover:opacity-88 transition-apple active:scale-98 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                  {loading ? (
                    <div className="w-5 h-5 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                  ) : (
                    <>
                      Sign In
                      <ArrowRight className="w-4 h-4" />
                    </>
                  )}
                </button>
              </form>

              {/* Footer */}
              <p className="text-center text-[13px] text-tertiary mt-8">
                Don&apos;t have an account?{' '}
                <a href="/register" className="text-[#25D366] font-medium hover:underline">
                  Sign up
                </a>
              </p>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
