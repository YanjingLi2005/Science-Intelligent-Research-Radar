import { useEffect, useState, type ReactNode } from 'react';
import { translateInterface } from '../lib/interfaceTranslations';
import { LanguageContext, type Locale } from './languageState';

const LANGUAGE_KEY = 'research-radar-login-language';

// Keep the original interface strings so switching back never changes user data.
const originalText = new WeakMap<Text, { source: string; rendered: string }>();
const originalAttributes = new WeakMap<Element, Map<string, { source: string; rendered: string }>>();
const attributes = ['placeholder', 'title', 'aria-label', 'alt'];

function localizeNode(node: Node, locale: Locale) {
  if (node.nodeType === Node.TEXT_NODE) {
    const textNode = node as Text;
    if (textNode.parentElement?.closest('[data-i18n-manual], script, style, [contenteditable="true"]')) return;
    const current = textNode.nodeValue ?? '';
    const old = originalText.get(textNode);
    const source = old && current === old.rendered ? old.source : current;
    const rendered = locale === 'en' ? translateInterface(source) : source;
    originalText.set(textNode, { source, rendered });
    if (rendered !== current) textNode.nodeValue = rendered;
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;
  const element = node as Element;
  if (element.closest('[data-i18n-manual], script, style, [contenteditable="true"]')) return;
  const saved = originalAttributes.get(element) ?? new Map<string, { source: string; rendered: string }>();
  for (const attribute of attributes) {
    const current = element.getAttribute(attribute);
    if (current === null) continue;
    const old = saved.get(attribute);
    const source = old && current === old.rendered ? old.source : current;
    const rendered = locale === 'en' ? translateInterface(source) : source;
    saved.set(attribute, { source, rendered });
    if (rendered !== current) element.setAttribute(attribute, rendered);
  }
  originalAttributes.set(element, saved);
  for (const child of element.childNodes) localizeNode(child, locale);
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(() => window.localStorage.getItem(LANGUAGE_KEY) === 'en' ? 'en' : 'zh');

  useEffect(() => {
    window.localStorage.setItem(LANGUAGE_KEY, locale);
    document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en';
    const root = document.getElementById('root');
    if (!root) return;
    localizeNode(root, locale);
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'characterData') localizeNode(record.target, locale);
        if (record.type === 'attributes') localizeNode(record.target, locale);
        for (const node of record.addedNodes) localizeNode(node, locale);
      }
    });
    observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: attributes });
    return () => observer.disconnect();
  }, [locale]);

  return <LanguageContext.Provider value={{ locale, setLocale, toggleLanguage: () => setLocale(locale === 'zh' ? 'en' : 'zh'), t: (text) => locale === 'en' ? translateInterface(text) : text }}>
    {children}
  </LanguageContext.Provider>;
}
