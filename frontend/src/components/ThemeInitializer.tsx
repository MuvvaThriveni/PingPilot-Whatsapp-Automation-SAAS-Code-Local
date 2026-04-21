'use client'

import { useEffect } from 'react'

export default function ThemeInitializer() {
  useEffect(() => {
    const storedTheme = localStorage.getItem('theme')
    const theme = storedTheme === 'light' ? 'light' : 'dark'
    document.documentElement.setAttribute('data-theme', theme)
  }, [])

  return null
}
