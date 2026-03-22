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
        dotClassName: 'bg-green-500',
        containerClassName: 'border-green-200 bg-green-50 text-green-700'
      }
    }

    return {
      label: 'Session Unknown',
      dotClassName: 'bg-gray-400',
      containerClassName: 'border-gray-200 bg-gray-50 text-gray-600'
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
    if (file.type.startsWith('image/')) return <ImageIcon className="h-8 w-8 text-blue-500" />
    return <FileText className="h-8 w-8 text-red-500" />
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Live Messaging</h1>
        <p className="text-gray-500 mt-1">Send real-time messages, images, or documents within the 24-hour session window</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Send Message</CardTitle>
          <CardDescription>
            Compose your message and choose who to send it to
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-6">
            {/* Mode Toggle */}
            <div className="flex space-x-2">
              <Button
                type="button"
                variant={mode === 'single' ? 'default' : 'outline'}
                onClick={() => setMode('single')}
                className="flex-1"
              >
                <User className="mr-2 h-4 w-4" />
                Single Contact
              </Button>
              <Button
                type="button"
                variant={mode === 'bulk' ? 'default' : 'outline'}
                onClick={() => setMode('bulk')}
                className="flex-1"
              >
                <Users className="mr-2 h-4 w-4" />
                Bulk Send (CSV)
              </Button>
            </div>

            {/* Message */}
            <div className="space-y-2">
              <Label htmlFor="message">Message</Label>
              <Textarea
                id="message"
                placeholder="Type your message..."
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                rows={3}
              />
              <p className="text-sm text-gray-500">
                You can send messages only within 24 hours of the user&apos;s last interaction.
              </p>
            </div>

            {/* File Upload */}
            <div className="space-y-2">
              <Label>Attachment</Label>
              {!file ? (
                <div
                  {...getRootProps()}
                  className={`
                    border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors
                    ${isDragActive ? 'border-green-500 bg-green-50' : 'border-gray-300 hover:border-gray-400'}
                  `}
                >
                  <input {...getInputProps()} />
                  <Upload className="h-10 w-10 mx-auto text-gray-400 mb-3" />
                  <p className="text-sm text-gray-600">
                    {isDragActive ? 'Drop the file here' : 'Drag & drop a file here, or click to select'}
                  </p>
                  <p className="text-xs text-gray-400 mt-2">
                    PDF, Images, Word, Excel (max 16MB)
                  </p>
                </div>
              ) : (
                <div className="flex items-center justify-between p-4 border rounded-lg bg-gray-50">
                  <div className="flex items-center space-x-3">
                    {getFileIcon()}
                    <div>
                      <p className="font-medium text-sm">{file.name}</p>
                      <p className="text-xs text-gray-500">
                        {(file.size / 1024 / 1024).toFixed(2)} MB
                      </p>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => setFile(null)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              )}
            </div>

            {/* Recipient - Single Mode */}
            {mode === 'single' && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="recipient">Recipient WhatsApp Number (with country code)</Label>
                  <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${sessionBadge.containerClassName}`}>
                    <span className={`mr-1.5 h-2 w-2 rounded-full ${sessionBadge.dotClassName}`} />
                    {sessionBadge.label}
                  </span>
                </div>
                <Input
                  id="recipient"
                  type="tel"
                  placeholder="e.g., 919876543210 (with country code)"
                  value={recipient}
                  onChange={(e) => setRecipient(e.target.value)}
                />
                <p className="text-xs text-gray-500">
                  Include country code without + or spaces
                </p>
              </div>
            )}

            {/* Contacts File - Bulk Mode */}
            {mode === 'bulk' && (
              <div className="space-y-2">
                <Label>Contacts File (CSV)</Label>
                {!contactsFile ? (
                  <div
                    {...getContactsRootProps()}
                    className={`
                      border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors
                      ${isContactsDragActive ? 'border-green-500 bg-green-50' : 'border-gray-300 hover:border-gray-400'}
                    `}
                  >
                    <input {...getContactsInputProps()} />
                    <FileSpreadsheet className="h-8 w-8 mx-auto text-gray-400 mb-2" />
                    <p className="text-sm text-gray-600">
                      {isContactsDragActive ? 'Drop the file here' : 'Upload a CSV with contacts'}
                    </p>
                    <p className="text-xs text-gray-400 mt-1">
                      Columns: Name, Phone/Mobile Number
                    </p>
                  </div>
                ) : (
                  <div className="flex items-center justify-between p-3 border rounded-lg bg-gray-50">
                    <div className="flex items-center space-x-3">
                      <FileSpreadsheet className="h-6 w-6 text-green-600" />
                      <div>
                        <p className="font-medium text-sm">{contactsFile.name}</p>
                        <p className="text-xs text-gray-500">
                          {parsingContacts ? 'Parsing...' : `${contacts.length} valid contacts`}
                        </p>
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => { setContactsFile(null); setContacts([]); }}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                )}
                {contacts.length > 0 && (
                  <div className="mt-2 max-h-32 overflow-y-auto border rounded p-2 bg-gray-50">
                    <p className="text-xs text-gray-500 mb-1">Preview (first 5):</p>
                    {contacts.slice(0, 5).map((c) => (
                      <div key={c.index} className="text-xs text-gray-700">
                        {c.name || 'No name'} - {c.phone}
                      </div>
                    ))}
                    {contacts.length > 5 && (
                      <p className="text-xs text-gray-400">...and {contacts.length - 5} more</p>
                    )}
                  </div>
                )}
              </div>
            )}

            {mode === 'bulk' && (
              <div className="flex justify-end">
                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${sessionBadge.containerClassName}`}>
                  <span className={`mr-1.5 h-2 w-2 rounded-full ${sessionBadge.dotClassName}`} />
                  {sessionBadge.label}
                </span>
              </div>
            )}

            {/* Submit */}
            <Button 
              type="submit" 
              className="w-full" 
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
                  {mode === 'bulk' ? `Send to ${contacts.length} Contacts` : 'Send Now'}
                </>
              )}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Use Cases */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Common Use Cases</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2 text-sm text-gray-600">
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-green-500 rounded-full" />
              <span>Send invoices and receipts to customers</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-green-500 rounded-full" />
              <span>Share reports and documents with team members</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-green-500 rounded-full" />
              <span>Send product brochures to potential clients</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-green-500 rounded-full" />
              <span>Deliver booking confirmations and tickets</span>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
