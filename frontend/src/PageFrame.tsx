import { useEffect, useRef, type ReactNode } from 'react'

export type PageStatus = Readonly<{
  kind: 'loading' | 'success' | 'error'
  message: string
}>

export type PageFrameProps = Readonly<{
  title: string
  heading: string
  className?: string
  description?: string
  routeFocusKey: string
  status?: PageStatus
  children: ReactNode
}>

export default function PageFrame({
  title,
  heading,
  className,
  description,
  routeFocusKey,
  status,
  children,
}: PageFrameProps) {
  const mainRef = useRef<HTMLElement>(null)

  useEffect(() => {
    document.title = title
  }, [title])

  useEffect(() => {
    mainRef.current?.focus()
  }, [routeFocusKey])

  return (
    <main ref={mainRef} className={`page-frame${className ? ` ${className}` : ''}`} tabIndex={-1} aria-labelledby="page-heading">
      <header className="page-heading">
        <p className="eyebrow">LINE MESSAGE PLAYGROUND</p>
        <h1 id="page-heading">{heading}</h1>
        {description && <p className="page-description">{description}</p>}
      </header>
      {status && (
        <p className={`page-status page-status-${status.kind}`} role={status.kind === 'error' ? 'alert' : 'status'}>
          {status.message}
        </p>
      )}
      <div className="page-content">{children}</div>
    </main>
  )
}
