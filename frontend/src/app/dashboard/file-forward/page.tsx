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
import { Upload, FileText, Image, X, Send, Loader2, Users, User, FileSpreadsheet } from 'lucide-react'

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
      } catch (error: any) {
        toast({
          title: 'Parse failed',
          description: error.response?.data?.error || 'Failed to parse contacts file',
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
      toast({ title: 'Error', description: 'Please select a file', variant: 'destructive' })
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
        toast({ title: 'Success', description: 'File sent successfully!' })
      } else {
        const formData = new FormData()
        formData.append('file', file)
        formData.append('contactsFile', contactsFile!)
        if (message) formData.append('message', message)

        const res = await fileForward.sendBulk(formData)
        toast({ 
          title: 'Bulk send complete', 
          description: `Sent to ${res.data.sent_count} of ${res.data.total} contacts` 
        })
      }
      
      setFile(null)
      setRecipient('')
      setMessage('')
      setContactsFile(null)
      setContacts([])
    } catch (error: any) {
      toast({
        title: 'Failed to send',
        description: error.response?.data?.error || 'Something went wrong',
        variant: 'destructive'
      })
    } finally {
      setLoading(false)
    }
  }

  const getFileIcon = () => {
    if (!file) return null
    if (file.type.startsWith('image/')) return <Image className="h-8 w-8 text-blue-500" />
    return <FileText className="h-8 w-8 text-red-500" />
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">File Forwarding</h1>
        <p className="text-gray-500 mt-1">Send a PDF, image, or document via WhatsApp</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Send File</CardTitle>
          <CardDescription>
            Upload a file and choose recipients to send
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
                Single Recipient
              </Button>
              <Button
                type="button"
                variant={mode === 'bulk' ? 'default' : 'outline'}
                onClick={() => setMode('bulk')}
                className="flex-1"
              >
                <Users className="mr-2 h-4 w-4" />
                Bulk (Excel/CSV)
              </Button>
            </div>

            {/* File Upload */}
            <div className="space-y-2">
              <Label>File</Label>
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
                <Label htmlFor="recipient">Recipient WhatsApp Number</Label>
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
                <Label>Contacts File (Excel/CSV)</Label>
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
                      {isContactsDragActive ? 'Drop the file here' : 'Upload Excel or CSV with contacts'}
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

            {/* Message */}
            <div className="space-y-2">
              <Label htmlFor="message">Message (Optional)</Label>
              <Textarea
                id="message"
                placeholder="Add a caption or message..."
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                rows={3}
              />
            </div>

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
