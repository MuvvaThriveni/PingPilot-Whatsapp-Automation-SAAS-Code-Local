'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { signInWithEmailAndPassword } from 'firebase/auth'
import { auth } from '@/lib/firebase'
import { Mail, Lock, Eye, EyeOff, MessageSquare, ChevronRight, User as Bot } from 'lucide-react'
import { motion } from 'framer-motion'

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
        setError('Invalid email or password. Please try again.')
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
    <div className="min-h-screen bg-[#f3f4f6] flex items-center justify-center p-4 lg:p-8">
      <div className="w-full max-w-[1100px] bg-white rounded-[2.5rem] shadow-2xl overflow-hidden flex flex-col lg:flex-row min-h-[700px]">

        {/* Left Side: Visuals & Branding */}
        <div className="lg:w-[45%] bg-gradient-to-br from-[#00a884] via-[#00a884] to-[#008f6f] p-8 lg:p-12 relative flex flex-col justify-between overflow-hidden">
          {/* Decorative Blobs with Animation */}
          <motion.div
            animate={{
              scale: [1, 1.1, 1],
              rotate: [0, 45, 0],
            }}
            transition={{ duration: 15, repeat: Infinity, ease: "linear" }}
            className="absolute top-[-10%] right-[-10%] w-[400px] h-[400px] bg-white/10 rounded-full blur-3xl pointer-events-none"
          />
          <motion.div
            animate={{
              scale: [1, 1.2, 1],
              x: [0, 20, 0],
            }}
            transition={{ duration: 20, repeat: Infinity, ease: "linear" }}
            className="absolute bottom-[-10%] left-[-10%] w-[350px] h-[350px] bg-black/10 rounded-full blur-3xl pointer-events-none"
          />

          <div className="relative z-10">
            {/* Logo */}
            <div className="flex items-center gap-2.5 mb-10 group">
              <div className="w-10 h-10 bg-white/20 rounded-lg flex items-center justify-center backdrop-blur-sm">
                <MessageSquare className="w-6 h-6 text-white" />
              </div>
              <span className="text-xl font-bold text-white tracking-tight">WappFlow</span>
            </div>

            {/* Content */}
            <motion.div
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.6, ease: "easeOut" }}
              className="space-y-4"
            >
              <h1 className="text-5xl lg:text-6xl font-bold text-white leading-tight tracking-tight">
                Welcome <br />
                <span className="text-emerald-100">Back!</span>
              </h1>
              <p className="text-emerald-50/80 text-lg max-w-[320px] leading-relaxed">
                Your WhatsApp campaigns are waiting. Sign in to engage your audience.
              </p>
            </motion.div>
          </div>

          {/* Phone Mockup / Chat Animation */}
          <div className="relative z-10 mt-12 mb-[-160px] lg:mb-[-180px] flex justify-center perspective-1000">
            <motion.div
              initial={{ y: 200, rotateX: 20, opacity: 0 }}
              animate={{ y: 0, rotateX: 0, opacity: 1 }}
              transition={{ delay: 0.5, duration: 1, ease: "circOut" }}
              className="w-[300px] h-[580px] bg-[#121b22] rounded-[3.5rem] border-8 border-[#232d36] shadow-2xl p-4 overflow-hidden ring-1 ring-white/10"
            >
              {/* Chat Header */}
              <div className="bg-[#121b22] pb-3 border-b border-white/5 flex items-center gap-3 mb-6 px-1">
                <div className="w-10 h-10 rounded-full bg-[#00a884] flex items-center justify-center text-sm text-white font-bold shadow-inner">
                  <Bot className="w-5 h-5" />
                </div>
                <div className="flex-1">
                  <div className="text-xs font-bold text-white tracking-wide">WappFlow Bot</div>
                  <div className="flex items-center gap-1.5">
                    <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                    <span className="text-[10px] font-medium text-white/40">online</span>
                  </div>
                </div>
              </div>

              {/* Chat Content */}
              <div className="flex flex-col gap-4 px-1 pt-2">
                <ChatBubble
                  text="Hi! Ready to launch your campaign?"
                  type="incoming"
                  delay={1.5}
                />
                <ChatBubble
                  text="Yes! Send it now"
                  type="outgoing"
                  delay={3}
                />
                <ChatBubble
                  text="Campaign sent! Great work."
                  type="incoming"
                  isCard
                  delay={4.5}
                />
                <motion.div
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 6, duration: 0.5 }}
                  className="bg-white/5 border border-white/10 self-start p-3 rounded-2xl flex gap-1.5"
                >
                  <div className="w-1.5 h-1.5 bg-emerald-500/50 rounded-full animate-bounce" />
                  <div className="w-1.5 h-1.5 bg-emerald-500/50 rounded-full animate-bounce [animation-delay:0.2s]" />
                  <div className="w-1.5 h-1.5 bg-emerald-500/50 rounded-full animate-bounce [animation-delay:0.4s]" />
                </motion.div>
              </div>
            </motion.div>
          </div>
        </div>

        {/* Right Side: Login Form */}
        <div className="lg:w-[55%] p-8 lg:p-24 flex flex-col justify-center items-center bg-white">
          <div className="w-full max-w-[420px] space-y-12">
            <div className="space-y-2 text-center lg:text-left">
              <h2 className="text-3xl font-bold text-gray-900 tracking-tight flex items-center justify-center lg:justify-start">
                Sign In<span className="text-[#00a884] ml-0.5">.</span>
              </h2>
              <p className="text-gray-500 font-medium">
                Access your WappFlow dashboard
              </p>
            </div>

            {error && (
              <motion.div
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                className="p-4 bg-red-50 border-l-4 border-red-500 text-red-700 text-sm rounded-r-lg font-medium"
              >
                {error}
              </motion.div>
            )}

            <form onSubmit={handleLogin} className="space-y-6">
              <div className="space-y-2">
                <label className="text-sm font-semibold text-gray-700 px-1">Email address</label>
                <div className="relative group text-gray-400 focus-within:text-[#00a884] transition-colors">
                  <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 transition-transform group-focus-within:scale-110" />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    required
                    className="w-full bg-gray-50 pl-12 pr-4 py-4 rounded-xl border border-gray-100 placeholder:text-gray-300 text-gray-900 focus:outline-none focus:ring-2 focus:ring-[#00a884]/20 focus:bg-white focus:border-[#00a884] transition-all"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-semibold text-gray-700 px-1">Password</label>
                <div className="relative group text-gray-400 focus-within:text-[#00a884] transition-colors">
                  <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 transition-transform group-focus-within:scale-110" />
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Enter your password"
                    required
                    className="w-full bg-gray-50 pl-12 pr-12 py-4 rounded-xl border border-gray-100 placeholder:text-gray-300 text-gray-900 focus:outline-none focus:ring-2 focus:ring-[#00a884]/20 focus:bg-white focus:border-[#00a884] transition-all"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-4 top-1/2 -translate-y-1/2 hover:text-gray-600 transition-colors"
                  >
                    {showPassword ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                  </button>
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full group bg-[#00a884] hover:bg-[#008f6f] text-white py-4 rounded-xl font-bold text-lg shadow-lg shadow-emerald-200/50 hover:shadow-emerald-300/50 transition-all active:scale-[0.98] disabled:opacity-70 disabled:pointer-events-none overflow-hidden relative"
              >
                <div className="relative z-10 flex items-center justify-center gap-2">
                  {loading ? (
                    <div className="w-6 h-6 border-3 border-white/30 border-t-white rounded-full animate-spin" />
                  ) : (
                    <>
                      Sign In
                      <ChevronRight className="w-5 h-5 transition-transform group-hover:translate-x-1" />
                    </>
                  )}
                </div>
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent -translate-x-[120%] group-hover:translate-x-[120%] transition-transform duration-700" />
              </button>
            </form>

          </div>
        </div>
      </div>
    </div>
  )
}

function ChatBubble({ text, type, delay, isCard }: { text: string; type: 'incoming' | 'outgoing'; delay: number; isCard?: boolean }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ delay, duration: 0.4 }}
      className={`max-w-[85%] p-3 rounded-2xl text-[11px] leading-relaxed shadow-sm ${type === 'incoming'
        ? 'bg-white/10 text-white rounded-tl-sm self-start'
        : 'bg-[#00a884] text-white rounded-tr-sm self-end'
        } ${isCard ? 'border border-white/10 bg-white/5' : ''}`}
    >
      {isCard ? (
        <div className="space-y-2">
          <div className="w-full h-12 bg-white/10 rounded-lg flex items-center justify-center text-white/20">
            <MessageSquare className="w-6 h-6" />
          </div>
          <p className="font-medium">{text}</p>
          <div className="flex justify-between items-center pt-1 border-t border-white/5 opacity-50 text-[8px]">
            <span>Delivered to 2,400 contacts</span>
            <span>09:41</span>
          </div>
        </div>
      ) : (
        <>
          <p>{text}</p>
          <div className="text-[8px] opacity-40 text-right mt-1">09:4{type === 'incoming' ? '0' : '1'}</div>
        </>
      )}
    </motion.div>
  )
}
