'use client'

import { useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import toast from 'react-hot-toast'
import { apiClient } from '@/lib/api-client'
import { AudioRecorder } from '@/components/AudioRecorder'
import { LiveTranscriptPanel } from '@/components/LiveTranscriptPanel'

const meetingSetupSchema = z.object({
  title: z.string().optional(),
  meeting_context: z.string().min(10, 'Please describe what this meeting is about (min 10 chars)'),
})
type MeetingSetupForm = z.infer<typeof meetingSetupSchema>

type PageState = 'setup' | 'recording' | 'stopped'

export default function LiveMeetingPage() {
  const router = useRouter()
  const [pageState, setPageState] = useState<PageState>('setup')
  const [captureTabAudio, setCaptureTabAudio] = useState(false)
  const [meetingId, setMeetingId] = useState<string | null>(null)
  const [transcriptChunks, setTranscriptChunks] = useState<string[]>([])
  const [errors, setErrors] = useState<string[]>([])

  const {
    register,
    handleSubmit,
    getValues,
    formState: { errors: formErrors, isSubmitting },
  } = useForm<MeetingSetupForm>({ resolver: zodResolver(meetingSetupSchema) })

  const onStartSetup = async (data: MeetingSetupForm) => {
    try {
      const meeting = await apiClient.post('/api/v1/meetings', {
        title: data.title || null,
        meeting_context: data.meeting_context,
      })
      setMeetingId(meeting.id)
      setPageState('recording')
      toast.success('Meeting started! Recording when you press Start.')
    } catch (err: any) {
      toast.error(err.message || 'Failed to create meeting')
    }
  }

  const handlePartialTranscript = useCallback((text: string) => {
    if (text.trim()) {
      setTranscriptChunks((prev) => [...prev, text])
    }
  }, [])

  const handleError = useCallback((msg: string) => {
    setErrors((prev) => [...prev, msg])
    toast.error(msg, { duration: 6000 })
  }, [])

  const handleStopped = useCallback(() => {
    setPageState('stopped')
    toast.success('Recording stopped. Generating your MOM — this takes 10–30 seconds.')
    if (meetingId) {
      // Navigate to the meeting page so the user can see the MOM once ready
      setTimeout(() => router.push(`/meetings/${meetingId}`), 2000)
    }
  }, [meetingId, router])

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Live Recording</h1>
          <p className="page-subtitle">
            Capture your meeting audio — the AI transcribes and generates MOM
            automatically when you stop.
          </p>
        </div>
      </div>

      {/* ── SETUP PHASE ────────────────────────────────────────────────────── */}
      {pageState === 'setup' && (
        <div style={{ maxWidth: 640 }}>
          <div className="card card-body">
            <h2 className="mom-section-header">Set up your meeting</h2>

            <form onSubmit={handleSubmit(onStartSetup)} noValidate>
              <div className="form-group" style={{ marginBottom: 20 }}>
                <label htmlFor="meeting-title" className="form-label">
                  Meeting title <span style={{ color: 'var(--color-text-faint)' }}>(optional)</span>
                </label>
                <input
                  id="meeting-title"
                  type="text"
                  className="form-input"
                  placeholder="e.g. Q3 Sprint Planning"
                  {...register('title')}
                />
              </div>

              <div className="form-group" style={{ marginBottom: 24 }}>
                <label htmlFor="meeting-context" className="form-label">
                  Meeting context <span style={{ color: 'var(--color-danger)', fontSize: 11 }}>required</span>
                </label>
                <textarea
                  id="meeting-context"
                  className="form-textarea"
                  placeholder="Briefly describe the meeting&apos;s purpose, project, and key attendees. The AI uses this to focus the MOM and retrieve relevant context from past meetings."
                  {...register('meeting_context')}
                />
                {formErrors.meeting_context && (
                  <span className="form-error">{formErrors.meeting_context.message}</span>
                )}
                <p className="form-hint">
                <strong>Example:</strong> Weekly backend sync for the Meeting Memory SaaS
                project. Attendees: Gaurang (lead), Priya (backend), Ankit (DevOps).
                </p>
              </div>

              {/* Tab audio toggle */}
              <div style={{ marginBottom: 28 }}>
                <div className="alert alert-info" style={{ marginBottom: 16 }}>
                  <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" style={{ flexShrink: 0, marginTop: 2 }}>
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd"/>
                  </svg>
                  <div>
                    <strong>Microphone vs. Tab Audio</strong>
                    <br />
                    By default, only your microphone is captured. If your meeting platform
                    (Zoom, Google Meet) is running in another tab, enable &quot;Tab Audio&quot; to
                    also capture remote participants. <em>Note: cannot capture phone-line
                    participants or physical room speakers.</em>
                  </div>
                </div>

                <label
                  className="capture-toggle"
                  htmlFor="tab-audio-toggle"
                  style={{ cursor: 'pointer' }}
                >
                  <div
                    className={`toggle-track${captureTabAudio ? ' active' : ''}`}
                    id="tab-audio-toggle"
                    role="switch"
                    aria-checked={captureTabAudio}
                    tabIndex={0}
                    onClick={() => setCaptureTabAudio((v) => !v)}
                    onKeyDown={(e) => e.key === 'Enter' && setCaptureTabAudio((v) => !v)}
                  >
                    <div className="toggle-thumb" />
                  </div>
                  <span className="toggle-label">
                    Also capture browser tab audio (for Zoom/Meet in this browser)
                  </span>
                </label>
              </div>

              <button
                id="create-meeting-btn"
                type="submit"
                className="btn-primary btn-large"
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <span className="spin" style={{ width: 16, height: 16 }} />
                    Setting up…
                  </>
                ) : (
                  <>
                    <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
                      <path fillRule="evenodd" d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z" clipRule="evenodd"/>
                    </svg>
                    Continue to Recording
                  </>
                )}
              </button>
            </form>
          </div>
        </div>
      )}

      {/* ── RECORDING PHASE ────────────────────────────────────────────────── */}
      {pageState === 'recording' && meetingId && (
        <div>
          <div className="live-page-grid">
            {/* Left: controls */}
            <div className="card card-body">
              <h2 className="mom-section-header">
                {getValues('title') || 'Recording in progress'}
              </h2>
              <p style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 20 }}>
                {getValues('meeting_context')}
              </p>

              <AudioRecorder
                meetingId={meetingId}
                captureTabAudio={captureTabAudio}
                onPartialTranscript={handlePartialTranscript}
                onError={handleError}
                onStopped={handleStopped}
              />

              {errors.length > 0 && (
                <div style={{ marginTop: 20 }}>
                  {errors.map((e, i) => (
                    <div key={i} className="alert alert-error" style={{ marginBottom: 8 }}>
                      ⚠️ {e}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Right: transcript */}
            <div className="card card-body">
              <h2 className="mom-section-header">Live Transcript</h2>
              <LiveTranscriptPanel chunks={transcriptChunks} />
            </div>
          </div>
        </div>
      )}

      {/* ── STOPPED PHASE ──────────────────────────────────────────────────── */}
      {pageState === 'stopped' && (
        <div className="empty-state">
          <div className="empty-icon">✅</div>
          <div className="empty-title">Recording complete</div>
          <p className="empty-description">
            Your MOM is being generated. Redirecting to the meeting page…
          </p>
          <div className="spinner-large" style={{ margin: '0 auto' }} />
        </div>
      )}
    </>
  )
}
