import { useEffect, useRef, type ReactNode } from 'react'

export type PageStatus = Readonly<{
  kind: 'loading' | 'success' | 'error'
  message: string
}>

export type PageFrameProps = Readonly<{
  title: string
  heading: string
  routeFocusKey: string
  status?: PageStatus
  children: ReactNode
}>

export default function PageFrame({
  title,
  heading,
  routeFocusKey,
  status,
  children,
}: PageFrameProps) {
  const headingRef = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    document.title = title
  }, [title])

  useEffect(() => {
    headingRef.current?.focus()
  }, [routeFocusKey])

  return (
    <main className="page-frame">
      <h1 ref={headingRef} tabIndex={-1}>{heading}</h1>
      {status && (
        <p className={`page-status page-status-${status.kind}`} role={status.kind === 'error' ? 'alert' : 'status'}>
          {status.message}
        </p>
      )}
      <div className="page-content">{children}</div>
    </main>
  )
}
