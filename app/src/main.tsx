import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { ThemeProvider } from 'next-themes'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'
import { LanguageProvider } from './contexts/LanguageContext'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
        <LanguageProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </LanguageProvider>
      </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
)
