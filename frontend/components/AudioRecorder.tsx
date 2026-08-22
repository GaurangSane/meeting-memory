/**
 * components/AudioRecorder.tsx
 *
 * Browser binary audio streamer — Phase 9, Step 9.1 (PLAN_WEB_SAAS.md).
 *
 * Flow:
 *   1. On Start: POST /api/v1/meetings/{id}/ws-ticket (authenticated REST)
 *      to get a short-lived (30s) single-use ticket.
 *   2. Open WebSocket with that ticket in the query string (NOT the JWT).
 *      The JWT must never appear in the WS URL — it will end up in proxy
 *      access logs. The 30s ticket limits blast radius of a logged URL.
 *   3. getUserMedia (mic) + optionally getDisplayMedia (tab audio, for
 *      Zoom/Meet running in another browser tab) → merge via AudioContext
 *      if both are enabled. The video track from getDisplayMedia is
 *      discarded immediately — audio only.
 *   4. MediaRecorder on the merged stream, rotated every 5s.
 *      Each Blob is sent as a binary WebSocket frame via arrayBuffer().
 *   5. On Stop: send {"type":"stop"} text control frame, close streams,
 *      close WebSocket, invoke onStopped callback.
 *
 * Capability gap (from §1 of the plan):
 *   - Browsers cannot capture OS loopback audio (the way sounddevice could).
 *   - getUserMedia only grants mic access.
 *   - The tab-audio toggle (getDisplayMedia) approximates the old loopback
 *     for browser-based meetings (Zoom in a tab) but cannot capture
 *     phone-line participants or physical room speakers.
 *   This limitation is stated in the UI, not silently swallowed.
 */

'use client'

import React, { useRef, useState, useCallback, useEffect, ReactNode } from 'react'
import type { JSX } from 'react'
import { apiClient } from '@/lib/api-client'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Props {
  meetingId: string
  captureTabAudio: boolean
  onPartialTranscript: (text: string) => void
  onError: (message: string) => void
  onStopped?: () => void
}

function isLocalWsUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1'
  } catch {
    return false
  }
}

function getWebSocketBase(): string {
  const configuredWsBase = process.env.NEXT_PUBLIC_WS_URL || ''

  if (typeof window === 'undefined') {
    return configuredWsBase || 'ws://localhost:8000'
  }

  const currentHostIsLocal =
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1'

  if (configuredWsBase && (!isLocalWsUrl(configuredWsBase) || currentHostIsLocal)) {
    return configuredWsBase
  }

  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${wsProtocol}//${window.location.host}`
}

// ── Component ─────────────────────────────────────────────────────────────────

export function AudioRecorder({
  meetingId,
  captureTabAudio,
  onPartialTranscript,
  onError,
  onStopped,
}: Props) {
  const [isRecording, setIsRecording] = useState(false)
  const [isStarting, setIsStarting] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  const wsRef = useRef<WebSocket | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamsRef = useRef<MediaStream[]>([])
  const audioContextRef = useRef<AudioContext | null>(null)
  const chunkIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const recordingStartedAtRef = useRef<number | null>(null)
  const rotationTickRef = useRef(0)

  useEffect(() => {
    if (!isRecording) {
      setElapsedSeconds(0)
      return
    }

    const timerId = setInterval(() => {
      const startedAt = recordingStartedAtRef.current
      if (startedAt === null) return
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000))
    }, 1000)

    return () => clearInterval(timerId)
  }, [isRecording])

  const formattedElapsed = `${String(Math.floor(elapsedSeconds / 60)).padStart(2, '0')}:${String(
    elapsedSeconds % 60
  ).padStart(2, '0')}`

  // ── Start recording ────────────────────────────────────────────────────────
  const start = useCallback(async () => {
    console.log('[AudioRecorder] start clicked', { meetingId, captureTabAudio })
    setIsStarting(true)
    try {
      // Step 1: Mint a single-use WS ticket via authenticated REST.
      // We call the API (which attaches the Bearer token from memory) — the ticket
      // then travels in the WS URL, not the JWT. This is the security boundary.
      console.log('[AudioRecorder] requesting WS ticket', { meetingId })
      const { ticket } = await apiClient.post(
        `/api/v1/meetings/${meetingId}/ws-ticket`
      )
      console.log('[AudioRecorder] WS ticket minted', { meetingId, hasTicket: Boolean(ticket) })

      // Step 2: Open the WebSocket with the ticket in the query string.
      const wsBase = getWebSocketBase()
      const wsUrl = `${wsBase}/ws/meetings/${meetingId}/audio?ticket=${ticket}`
      console.log('[AudioRecorder] opening WebSocket', { meetingId, wsBase })
      const ws = new WebSocket(wsUrl)
      ws.binaryType = 'arraybuffer'
      wsRef.current = ws

      // Wire message handler before waiting for open
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data as string)
          if (msg.type === 'partial_transcript' && msg.text) {
            onPartialTranscript(msg.text)
          }
          if (msg.type === 'stt_error') {
            onError(`STT error: ${msg.message}`)
          }
        } catch {
          // Non-JSON frame — ignore
        }
      }

      ws.onerror = (event) => {
        console.error('[AudioRecorder] WebSocket error before open handler override', event)
        onError('WebSocket connection error. Check the backend is running.')
      }

      // Wait for the WebSocket handshake to complete
      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(
          () => reject(new Error('WebSocket connection timed out after 10 seconds')),
          10_000
        )
        ws.onopen = () => {
          clearTimeout(timeout)
          console.log('[AudioRecorder] WebSocket open', { meetingId })
          resolve()
        }
        ws.onerror = (e) => {
          clearTimeout(timeout)
          console.error('[AudioRecorder] WebSocket handshake failed', e)
          reject(new Error('WebSocket handshake failed'))
        }
      })

      // Step 3: Acquire media streams.
      console.log('[AudioRecorder] requesting microphone access', { meetingId })
      const micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 16000,
        },
      })
      console.log('[AudioRecorder] microphone access granted', {
        meetingId,
        audioTracks: micStream.getAudioTracks().length,
        trackStates: micStream.getAudioTracks().map((track) => ({
          label: track.label,
          enabled: track.enabled,
          muted: track.muted,
          readyState: track.readyState,
        })),
      })
      streamsRef.current.push(micStream)
      let finalStream: MediaStream = micStream

      if (captureTabAudio) {
        // getDisplayMedia lets us capture a browser tab's audio.
        // Video track is discarded immediately — we only want the audio.
        console.log('[AudioRecorder] requesting tab audio access', { meetingId })
        const tabStream = await navigator.mediaDevices.getDisplayMedia({
          video: true,
          audio: true,
        })
        console.log('[AudioRecorder] tab capture granted', {
          meetingId,
          audioTracks: tabStream.getAudioTracks().length,
          videoTracks: tabStream.getVideoTracks().length,
        })
        streamsRef.current.push(tabStream)

        // Mix mic + tab audio into a single stream via AudioContext.
        const audioContext = new AudioContext({ sampleRate: 16000 })
        audioContextRef.current = audioContext
        const dest = audioContext.createMediaStreamDestination()

        audioContext.createMediaStreamSource(micStream).connect(dest)

        const tabAudioTrack = tabStream.getAudioTracks()[0]
        if (tabAudioTrack) {
          audioContext
            .createMediaStreamSource(new MediaStream([tabAudioTrack]))
            .connect(dest)
        }

        // Discard video — we never need it.
        tabStream.getVideoTracks().forEach((t) => t.stop())

        finalStream = dest.stream
      }

      // Step 4: MediaRecorder on the final (possibly mixed) stream.
      // A brand-new MediaRecorder is created for every chunk so every WebM blob
      // includes its own EBML header and can be decoded independently.
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : ''

      const createRecorder = () => {
        console.log('[AudioRecorder] creating MediaRecorder', {
          meetingId,
          mimeType: mimeType || '(browser default)',
          streamAudioTracks: finalStream.getAudioTracks().length,
        })
        const recorder = new MediaRecorder(
          finalStream,
          mimeType ? { mimeType } : undefined
        )

        recorder.ondataavailable = async (e) => {
          console.log('[AudioRecorder] ondataavailable fired', {
            meetingId,
            blobSize: e.data.size,
            blobType: e.data.type,
            wsReadyState: ws.readyState,
          })
          if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
            // Converts the Blob to ArrayBuffer and sends as a binary WS frame.
            const buffer = await e.data.arrayBuffer()
            ws.send(buffer)
            console.log('[AudioRecorder] sent audio chunk', {
              meetingId,
              bytes: buffer.byteLength,
            })
          } else {
            console.warn('[AudioRecorder] audio chunk not sent', {
              meetingId,
              blobSize: e.data.size,
              wsReadyState: ws.readyState,
            })
          }
        }

        recorder.onerror = (e) => {
          console.error('[AudioRecorder] MediaRecorder error event', e)
          onError(`MediaRecorder error: ${(e as any).error?.message || 'unknown'}`)
        }

        return recorder
      }

      const startNewRecorder = () => {
        const recorder = createRecorder()
        recorder.start()
        recorderRef.current = recorder
        console.log('[AudioRecorder] MediaRecorder started', {
          meetingId,
          state: recorder.state,
        })
      }

      startNewRecorder()
      recordingStartedAtRef.current = Date.now()
      rotationTickRef.current = 0
      setIsRecording(true)

      // Rotate every 5s so each blob is a self-contained WebM file.
      const intervalId = setInterval(() => {
        const now = Date.now()
        const startedAt = recordingStartedAtRef.current
        const elapsedSeconds =
          startedAt === null ? null : Number(((now - startedAt) / 1000).toFixed(2))
        rotationTickRef.current += 1
        console.log('[AudioRecorder] rotation interval tick', {
          meetingId,
          tick: rotationTickRef.current,
          timestamp: new Date(now).toISOString(),
          elapsedSeconds,
          recorderState: recorderRef.current?.state,
          wsReadyState: ws.readyState,
        })

        if (recorderRef.current && recorderRef.current.state === 'recording') {
          console.log('[AudioRecorder] rotating MediaRecorder chunk', { meetingId })
          recorderRef.current.stop()
          startNewRecorder()
        } else {
          console.warn('[AudioRecorder] rotation skipped; recorder not recording', {
            meetingId,
            recorderState: recorderRef.current?.state,
          })
        }
      }, 5000)
      chunkIntervalRef.current = intervalId
    } catch (err: any) {
      console.error('[AudioRecorder] start failed', err)
      onError(err?.message || 'Failed to start recording. Check microphone permissions.')
    } finally {
      setIsStarting(false)
    }
  }, [meetingId, captureTabAudio, onPartialTranscript, onError])

  // ── Stop recording ─────────────────────────────────────────────────────────
  const stop = useCallback(async () => {
    console.log('[AudioRecorder] stop clicked', {
      meetingId,
      recorderState: recorderRef.current?.state,
      wsReadyState: wsRef.current?.readyState,
    })

    const recorder = recorderRef.current
    const ws = wsRef.current

    const waitForFinalBlob = async () => {
      if (!recorder || recorder.state === 'inactive') {
        console.log('[AudioRecorder] no active MediaRecorder to drain', { meetingId })
        return
      }

      await new Promise<void>((resolve) => {
        let settled = false
        const timeoutId = setTimeout(() => {
          if (settled) return
          settled = true
          console.warn('[AudioRecorder] final blob timeout; proceeding with stop', {
            meetingId,
            recorderState: recorder.state,
            wsReadyState: ws?.readyState,
          })
          resolve()
        }, 3000)

        recorder.ondataavailable = async (e) => {
          console.log('[AudioRecorder] final ondataavailable fired', {
            meetingId,
            blobSize: e.data.size,
            blobType: e.data.type,
            wsReadyState: ws?.readyState,
          })

          try {
            if (e.data.size > 0 && ws?.readyState === WebSocket.OPEN) {
              const buffer = await e.data.arrayBuffer()
              ws.send(buffer)
              console.log('[AudioRecorder] sent audio chunk', {
                meetingId,
                bytes: buffer.byteLength,
                final: true,
                wsReadyState: ws.readyState,
              })
            } else {
              console.warn('[AudioRecorder] audio chunk not sent', {
                meetingId,
                blobSize: e.data.size,
                final: true,
                wsReadyState: ws?.readyState,
              })
            }
          } finally {
            if (!settled) {
              settled = true
              clearTimeout(timeoutId)
              resolve()
            }
          }
        }

        recorder.stop()
        console.log('[AudioRecorder] MediaRecorder stop requested; waiting for final blob', {
          meetingId,
        })
      })
    }

    // Stop the MediaRecorder (triggers any final ondataavailable)
    await waitForFinalBlob()
    recorderRef.current = null

    if (chunkIntervalRef.current) {
      clearInterval(chunkIntervalRef.current)
      chunkIntervalRef.current = null
    }
    recordingStartedAtRef.current = null

    // Release all media tracks to turn off the browser's recording indicator
    streamsRef.current.forEach((s) => s.getTracks().forEach((t) => t.stop()))
    streamsRef.current = []

    // Close AudioContext if we created one for tab audio mixing
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {})
      audioContextRef.current = null
    }

    // Step 5: Send the stop control frame, then close the socket.
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'stop' }))
      console.log('[AudioRecorder] stop control frame sent', { meetingId })
      ws.close()
      console.log('[AudioRecorder] WebSocket close requested', { meetingId })
    } else {
      console.warn('[AudioRecorder] stop control frame not sent; WebSocket not open', {
        meetingId,
        wsReadyState: ws?.readyState,
      })
    }
    wsRef.current = null

    setIsRecording(false)
    onStopped?.()
  }, [meetingId, onStopped])

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="recorder-controls">
      <button
        id="start-recording-btn"
        onClick={start}
        disabled={isRecording || isStarting}
        className="btn-primary btn-large"
      >
        {isStarting ? (
          <>
            <span className="spin" style={{ width: 16, height: 16 }} />
            Starting…
          </>
        ) : (
          <>
            <span className="btn-icon">▶</span>
            Start Recording
          </>
        )}
      </button>

      <button
        id="stop-recording-btn"
        onClick={stop}
        disabled={!isRecording}
        className="btn-danger btn-large"
      >
        <span className="btn-icon">■</span>
        Stop &amp; Generate MOM
      </button>

      {isRecording && (
        <div className="recording-indicator" role="status" aria-live="polite">
          <span className="pulse-dot" aria-hidden="true" />
          <span>Recording{captureTabAudio && ' + Tab Audio'}</span>
          <span className="recording-timer">{formattedElapsed}</span>
        </div>
      )}
    </div>
  )
}
