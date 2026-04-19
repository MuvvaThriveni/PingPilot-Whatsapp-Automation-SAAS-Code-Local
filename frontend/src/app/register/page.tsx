'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createUserWithEmailAndPassword } from 'firebase/auth'
import { auth } from '@/lib/firebase'
import { Mail, Lock, Eye, EyeOff, MessageSquare, ArrowRight, User } from 'lucide-react'

export default function RegisterPage() {
  const router = useRouter()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await createUserWithEmailAndPassword(auth, email, password)
      router.push('/dashboard')
    } catch (err: unknown) {
      const firebaseError = err as { code?: string }
      if (firebaseError.code === 'auth/email-already-in-use') {
        setError('This email is already registered.')
      } else if (firebaseError.code === 'auth/weak-password') {
        setError('Password must be at least 6 characters.')
      } else {
        setError('Registration failed. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-black flex items-center justify-center p-6">
      <div className="w-full max-w-[440px]">
        {/* Logo */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2.5 mb-6">
            <div className="w-10 h-10 bg-[#25D366]/10 rounded-lg flex items-center justify-center">
              <MessageSquare className="w-5 h-5 text-[#25D366]" />
            </div>
            <span className="text-lg font-semibold text-white tracking-tight">WappFlow</span>
          </div>
          <h1 className="text-[32px] font-bold text-white tracking-tight mb-2">
            Create an account
          </h1>
          <p className="text-secondary text-[15px]">
            Start automating your WhatsApp messages
          </p>
        </div>

        {/* Form Card */}
        <div className="bg-[#0a0a0a] rounded-2xl border border-white/[0.07] p-6 sm:p-8">
          {error && (
            <div className="mb-6 p-3 bg-red-500/10 border border-red-500/20 rounded-xl">
              <p className="text-[13px] text-red-400 font-medium">{error}</p>
            </div>
          )}

          <form onSubmit={handleRegister} className="space-y-5">
            {/* Name */}
            <div className="space-y-2">
              <label className="text-[13px] font-medium text-secondary">
                Full name
              </label>
              <div className="relative">
                <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-tertiary" />
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="John Doe"
                  required
                  className="w-full bg-[#111111] pl-11 pr-4 py-3 rounded-xl border border-white/[0.07] placeholder:text-tertiary text-white text-[14px] focus:outline-none focus:ring-2 focus:ring-[#25D366]/20 focus:border-[#25D366]/30 transition-all"
                />
              </div>
            </div>

            {/* Email */}
            <div className="space-y-2">
              <label className="text-[13px] font-medium text-secondary">
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
              <label className="text-[13px] font-medium text-secondary">
                Password
              </label>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-tertiary" />
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Create a password"
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
                  Create Account
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>
        </div>

        {/* Footer */}
        <p className="text-center text-[13px] text-tertiary mt-8">
          Already have an account?{' '}
          <a href="/login" className="text-[#25D366] font-medium hover:underline">
            Sign in
          </a>
        </p>
      </div>
    </div>
  )
}
