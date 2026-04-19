'use client'

import { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { fileForward } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { Upload, FileText, Image as ImageIcon, X, Send, Loader2, Users, User, FileSpreadsheet } from 'lucide-react'
import axios from 'axios'

interface Contact {
  index: number
  phone: string
  name: string
}

export default function FileForwardPage() {
  const { toast } = useToast()
  const [loading, setLoading] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [recipient, setRecipient] = useState('')
  const [message, setMessage] = useState('')
  const [mode, setMode] = useState<'single' | 'bulk'>('single')
  const [contactsFile, setContactsFile] = useState<File | null>(null)
  const [contacts, setContacts] = useState<Contact[]>([])
  const [parsingContacts, setParsingContacts] = useState(false)

  const [sessionStatus] = useState<'unknown' | 'active'>('unknown')

  const sessionBadge = (() => {
    if (sessionStatus === 'active') {
      return {
        label: 'Session Active',
        dotClassName: 'bg-[#25D366]',
        containerClassName: 'border-[#1a3a2a] bg-[#0a1a0f] text-[#25D366]'
      }
    }

    return {
      label: 'Session Unknown',
      dotClassName: 'bg-white/20',
      containerClassName: 'border-white/[0.06] bg-white/[0.02] text-secondary'
    }
  })()

  const onDrop = useCallback((acceptedFiles: File[]) => {
    if (acceptedFiles.length > 0) {
      setFile(acceptedFiles[0])
    }
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'image/*': ['.png', '.jpg', '.jpeg', '.gif'],
      'application/msword': ['.doc'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'application/vnd.ms-excel': ['.xls'],
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx']
    },
    maxFiles: 1,
    maxSize: 16 * 1024 * 1024
  })

  const handleContactsFileDrop = useCallback(async (acceptedFiles: File[]) => {
    if (acceptedFiles.length > 0) {
      const selectedFile = acceptedFiles[0]
      setContactsFile(selectedFile)
      setParsingContacts(true)

      try {
        const formData = new FormData()
        formData.append('contactsFile', selectedFile)
        const res = await fileForward.parseContacts(formData)
        setContacts(res.data.contacts)
        toast({ title: 'Contacts loaded', description: `Found ${res.data.total} valid contacts` })
      } catch (error: unknown) {
        toast({
          title: 'Parse failed',
          description: axios.isAxiosError(error) ? error.response?.data?.error || 'Failed to parse contacts file' : 'Failed to parse contacts file',
          variant: 'destructive'
        })
        setContactsFile(null)
      } finally {
        setParsingContacts(false)
      }
    }
  }, [toast])

  const { getRootProps: getContactsRootProps, getInputProps: getContactsInputProps, isDragActive: isContactsDragActive } = useDropzone({
    onDrop: handleContactsFileDrop,
    accept: {
      'application/vnd.ms-excel': ['.xls'],
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
      'text/csv': ['.csv']
    },
    maxFiles: 1
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!file) {
      toast({ title: 'Error', description: 'Please attach an image or document', variant: 'destructive' })
      return
    }

    if (mode === 'single' && !recipient) {
      toast({ title: 'Error', description: 'Please enter recipient number', variant: 'destructive' })
      return
    }

    if (mode === 'bulk' && !contactsFile) {
      toast({ title: 'Error', description: 'Please upload a contacts file', variant: 'destructive' })
      return
    }

    setLoading(true)

    try {
      if (mode === 'single') {
        const formData = new FormData()
        formData.append('file', file)
        formData.append('recipient', recipient)
        if (message) formData.append('message', message)

        await fileForward.send(formData)
        toast({ title: 'Success', description: 'Message sent successfully!' })
      } else {
        const formData = new FormData()
        formData.append('file', file)
        formData.append('contactsFile', contactsFile!)
        if (message) formData.append('message', message)

        const res = await fileForward.sendBulk(formData)
        toast({
          title: 'Bulk send queued',
          description: `Queued ${res.data.queued_count} of ${res.data.total} contacts`
        })
      }

      setFile(null)
      setRecipient('')
      setMessage('')
      setContactsFile(null)
      setContacts([])
    } catch (error: unknown) {
      toast({
        title: 'Failed to send',
        description: axios.isAxiosError(error) ? error.response?.data?.error || 'Something went wrong' : 'Something went wrong',
        variant: 'destructive'
      })
    } finally {
      setLoading(false)
    }
  }

  const getFileIcon = () => {
    if (!file) return null
    if (file.type.startsWith('image/')) return <ImageIcon className="h-8 w-8 text-blue-400" />
    return <FileText className="h-8 w-8 text-red-400" />
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <p className="text-eyebrow mb-2">Real-time Communication</p>
        <h1 className="text-section-title text-white">Live Messaging</h1>
        <p className="text-body text-[14px] mt-1">
          Send real-time messages, images, or documents within the 24-hour session window
        </p>
      </div>

      {/* Session Status */}
      <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium ${sessionBadge.containerClassName}`}>
        <span className={`w-2 h-2 rounded-full ${sessionBadge.dotClassName}`} />
        {sessionBadge.label}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Compose Message</CardTitle>
          <CardDescription>
            Choose your recipient and attach content to send
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-6">
            {/* Mode Toggle */}
            <div className="flex space-x-2 p-1 bg-white/[0.04] rounded-lg">
              <Button
                type="button"
                variant={mode === 'single' ? 'secondary' : 'ghost'}
                onClick={() => setMode('single')}
                className="flex-1"
              >
                <User className="mr-2 h-4 w-4" />
                Single
              </Button>
              <Button
                type="button"
                variant={mode === 'bulk' ? 'secondary' : 'ghost'}
                onClick={() => setMode('bulk')}
                className="flex-1"
              >
                <Users className="mr-2 h-4 w-4" />
                Bulk
              </Button>
            </div>

            {/* Message */}
            <div className="space-y-2">
              <Label htmlFor="message" className="text-[13px] font-medium text-secondary">Message (optional)</Label>
              <Textarea
                id="message"
                placeholder="Type your message..."
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                rows={3}
                className="bg-[#111111] border-white/[0.07] placeholder:text-tertiary text-white text-[14px] focus:ring-[#25D366]/20 focus:border-[#25D366]/30"
              />
              <p className="text-xs text-tertiary">
                Messages can only be sent within 24 hours of the user&apos;s last interaction.
              </p>
            </div>

            {/* File Upload */}
            <div className="space-y-2">
              <Label className="text-[13px] font-medium text-secondary">Attachment</Label>
              {!file ? (
                <div
                  {...getRootProps()}
                  className={`
                    border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-apple
                    ${isDragActive ? 'border-[#25D366] bg-[#25D366]/[0.04]' : 'border-white/[0.07] hover:border-white/[0.14] hover:bg-white/[0.02]'}
                  `}
                >
                  <input {...getInputProps()} />
                  <Upload className="h-10 w-10 mx-auto text-tertiary mb-3" />
                  <p className="text-sm text-secondary">
                    {isDragActive ? 'Drop the file here' : 'Drag & drop a file, or click to select'}
                  </p>
                  <p className="text-xs text-tertiary mt-2">
                    PDF, Images, Word, Excel (max 16MB)
                  </p>
                </div>
              ) : (
                <div className="flex items-center justify-between p-4 bg-[#111111] rounded-xl border border-white/[0.07]">
                  <div className="flex items-center space-x-3">
                    {getFileIcon()}
                    <div>
                      <p className="font-medium text-[13px] text-white">{file.name}</p>
                      <p className="text-xs text-tertiary">
                        {(file.size / 1024 / 1024).toFixed(2)} MB
                      </p>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => setFile(null)}
                    className="h-8 w-8"
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              )}
            </div>

            {/* Recipient - Single Mode */}
            {mode === 'single' && (
              <div className="space-y-2">
                <Label htmlFor="recipient" className="text-[13px] font-medium text-secondary">
                  Recipient WhatsApp Number
                </Label>
                <Input
                  id="recipient"
                  type="tel"
                  placeholder="e.g., 919876543210"
                  value={recipient}
                  onChange={(e) => setRecipient(e.target.value)}
                  className="bg-[#111111] border-white/[0.07] placeholder:text-tertiary text-white text-[14px] focus:ring-[#25D366]/20 focus:border-[#25D366]/30"
                />
                <p className="text-xs text-tertiary">
                  Include country code without + or spaces
                </p>
              </div>
            )}

            {/* Contacts File - Bulk Mode */}
            {mode === 'bulk' && (
              <div className="space-y-2">
                <Label className="text-[13px] font-medium text-secondary">Contacts File</Label>
                {!contactsFile ? (
                  <div
                    {...getContactsRootProps()}
                    className={`
                      border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-apple
                      ${isContactsDragActive ? 'border-[#25D366] bg-[#25D366]/[0.04]' : 'border-white/[0.07] hover:border-white/[0.14] hover:bg-white/[0.02]'}
                    `}
                  >
                    <input {...getContactsInputProps()} />
                    <FileSpreadsheet className="h-8 w-8 mx-auto text-tertiary mb-2" />
                    <p className="text-sm text-secondary">
                      {isContactsDragActive ? 'Drop the file here' : 'Upload Excel or CSV with contacts'}
                    </p>
                    <p className="text-xs text-tertiary mt-1">
                      Columns: Name, Phone Number
                    </p>
                  </div>
                ) : (
                  <div className="flex items-center justify-between p-3 bg-[#111111] rounded-xl border border-white/[0.07]">
                    <div className="flex items-center space-x-3">
                      <FileSpreadsheet className="h-6 w-6 text-[#25D366]" />
                      <div>
                        <p className="font-medium text-[13px] text-white">{contactsFile.name}</p>
                        <p className="text-xs text-tertiary">
                          {parsingContacts ? 'Parsing...' : `${contacts.length} valid contacts`}
                        </p>
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => { setContactsFile(null); setContacts([]); }}
                      className="h-8 w-8"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                )}
                {contacts.length > 0 && (
                  <div className="mt-2 max-h-32 overflow-y-auto border border-white/[0.07] rounded-xl p-3 bg-[#111111]">
                    <p className="text-xs text-tertiary mb-2">Preview (first 5):</p>
                    {contacts.slice(0, 5).map((c) => (
                      <div key={c.index} className="text-xs text-secondary py-1">
                        {c.name || 'No name'} — {c.phone}
                      </div>
                    ))}
                    {contacts.length > 5 && (
                      <p className="text-xs text-tertiary">...and {contacts.length - 5} more</p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Submit */}
            <Button
              type="submit"
              className="w-full btn-pill h-11"
              disabled={loading || !file || (mode === 'single' && !recipient) || (mode === 'bulk' && !contactsFile)}
            >
              {loading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {mode === 'bulk' ? `Sending to ${contacts.length} contacts...` : 'Sending...'}
                </>
              ) : (
                <>
                  <Send className="mr-2 h-4 w-4" />
                  {mode === 'bulk' ? `Send to ${contacts.length} Contacts` : 'Send Message'}
                </>
              )}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Use Cases */}
      <Card>
        <CardHeader>
          <CardTitle className="text-[15px]">Common Use Cases</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-3 text-[13px] text-secondary">
            <li className="flex items-center space-x-3">
              <div className="w-1.5 h-1.5 bg-[#25D366] rounded-full" />
              <span>Send invoices and receipts to customers</span>
            </li>
            <li className="flex items-center space-x-3">
              <div className="w-1.5 h-1.5 bg-[#25D366] rounded-full" />
              <span>Share reports and documents with team members</span>
            </li>
            <li className="flex items-center space-x-3">
              <div className="w-1.5 h-1.5 bg-[#25D366] rounded-full" />
              <span>Send product brochures to potential clients</span>
            </li>
            <li className="flex items-center space-x-3">
              <div className="w-1.5 h-1.5 bg-[#25D366] rounded-full" />
              <span>Deliver booking confirmations and tickets</span>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
