import { createContext, useContext } from 'react';

export type Locale = 'zh' | 'en';

export const LanguageContext = createContext<{
  locale: Locale;
  setLocale: (locale: Locale) => void;
  toggleLanguage: () => void;
  t: (text: string) => string;
} | null>(null);

export function useLanguage() {
  const value = useContext(LanguageContext);
  if (!value) throw new Error('LanguageProvider is missing');
  return value;
}
