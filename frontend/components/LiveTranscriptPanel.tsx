'use client'

import { useEffect, useRef } from 'react'

interface Props {
  chunks: string[]
}

/**
 * LiveTranscriptPanel
 *
 * Displays real-time partial transcript chunks as they arrive from the
 * WebSocket handler's "partial_transcript" messages. Animates each new
 * chunk in and auto-scrolls to the latest chunk.
 */
export function LiveTranscriptPanel({ chunks }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom whenever a new chunk arrives
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chunks])

  return (
    <div
      className="transcript-panel"
      role="log"
      aria-live="polite"
      aria-label="Live transcript"
    >
      {chunks.length === 0 ? (
        <div className="transcript-empty">
          <span>🎙️ Listening for speech…</span>
        </div>
      ) : (
        <>
          {chunks.map((text, i) => (
            <div key={i} className="transcript-chunk">
              <span
                style={{
                  color: 'var(--color-text-faint)',
                  fontSize: 11,
                  marginRight: 8,
                  userSelect: 'none',
                }}
              >
                [{String(i + 1).padStart(2, '0')}]
              </span>
              {text}
            </div>
          ))}
          <div ref={bottomRef} />
        </>
      )}
    </div>
  )
}
