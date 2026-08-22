'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { apiClient } from '@/lib/api-client'
import { useAuth } from '@/lib/auth'
import toast from 'react-hot-toast'
import { RefreshCw, Trash2 } from 'lucide-react'

interface Meeting {
  id: string
  title: string | null
  meeting_context: string
  status: 'recording' | 'processing' | 'completed' | 'failed'
  created_at: string
  completed_at: string | null
}

function StatusBadge({ status }: { status: Meeting['status'] }) {
  type StatusConfig = {
  className: string
  label: string
  showPulse: boolean
  showSpin: boolean
}

const configs: Record<Meeting['status'], StatusConfig> = {
  recording: {
    className: 'badge-recording',
    label: 'Recording',
    showPulse: true,
    showSpin: false,
  },
  processing: {
    className: 'badge-processing',
    label: 'Processing',
    showPulse: false,
    showSpin: true,
  },
  completed: {
    className: 'badge-completed',
    label: 'Completed',
    showPulse: false,
    showSpin: false,
  },
  failed: {
    className: 'badge-failed',
    label: 'Failed',
    showPulse: false,
    showSpin: false,
  },
}
  const c = configs[status] || configs.failed

  return (
    <span className={`badge ${c.className}`}>
      {c.showPulse && <span className="pulse-dot" aria-hidden="true" />}
      {c.showSpin && <span className="spin" aria-hidden="true" />}
      {c.label}
    </span>
  )
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function MeetingCardSkeleton() {
  return (
    <div className="card" style={{ padding: '24px' }}>
      <div className="skeleton skeleton-text" style={{ width: '60%' }} />
      <div className="skeleton skeleton-text" style={{ width: '90%', marginTop: 12 }} />
      <div className="skeleton skeleton-text" style={{ width: '75%' }} />
      <div className="skeleton skeleton-text" style={{ width: '40%', marginTop: 20 }} />
    </div>
  )
}

export default function MeetingsPage() {
  const { isLoading: authLoading } = useAuth()
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingMeetingId, setDeletingMeetingId] = useState<string | null>(null)
  const [retryingMeetingId, setRetryingMeetingId] = useState<string | null>(null)

  useEffect(() => {
    if (authLoading) return
    ;(async () => {
      try {
        const data = await apiClient.get('/api/v1/meetings')
        setMeetings(data)
      } catch (err: any) {
        setError(err.message || 'Failed to load meetings')
        toast.error('Could not load meetings')
      } finally {
        setIsLoading(false)
      }
    })()
  }, [authLoading])

  const handleDeleteMeeting = async (meeting: Meeting) => {
    const title = meeting.title || 'Untitled Meeting'
    const confirmed = window.confirm(
      `Delete "${title}"?\n\nThis will permanently remove the transcript, MOM, and learning embeddings for this meeting.`
    )
    if (!confirmed) return

    setDeletingMeetingId(meeting.id)
    try {
      await apiClient.delete(`/api/v1/meetings/${meeting.id}`)
      setMeetings((prev) => prev.filter((m) => m.id !== meeting.id))
      toast.success('Meeting deleted.')
    } catch (err: any) {
      toast.error(err.message || 'Could not delete meeting')
    } finally {
      setDeletingMeetingId(null)
    }
  }

  const handleRetryMeeting = async (meeting: Meeting) => {
    setRetryingMeetingId(meeting.id)
    try {
      const updated = await apiClient.post(`/api/v1/meetings/${meeting.id}/retry`, {})
      setMeetings((prev) =>
        prev.map((m) => (m.id === meeting.id ? { ...m, status: updated.status } : m))
      )
      toast.success('Retry started. Checking for MOM again.')
    } catch (err: any) {
      toast.error(err.message || 'Could not retry MOM generation')
    } finally {
      setRetryingMeetingId(null)
    }
  }

  return (
    <>
      {/* Page header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Past Meetings</h1>
          <p className="page-subtitle">
            Review and edit AI-generated minutes. Your edits teach the system to
            get better.
          </p>
        </div>
        <Link href="/meetings/live" className="btn-primary">
          <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z" clipRule="evenodd"/>
          </svg>
          New Recording
        </Link>
      </div>

      {/* Error state */}
      {error && (
        <div className="alert alert-error" style={{ marginBottom: 24 }}>
          ⚠️ {error}
        </div>
      )}

      {/* Loading skeletons */}
      {isLoading && (
        <div className="meetings-grid">
          {[...Array(6)].map((_, i) => (
            <MeetingCardSkeleton key={i} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && meetings.length === 0 && (
        <div className="empty-state">
          <div className="empty-icon">📋</div>
          <div className="empty-title">No meetings yet</div>
          <p className="empty-description">
            Start your first live recording session and the AI will generate
            structured minutes automatically.
          </p>
          <Link href="/meetings/live" className="btn-primary btn-large">
            Start recording
          </Link>
        </div>
      )}

      {/* Meetings grid */}
      {!isLoading && meetings.length > 0 && (
        <div className="meetings-grid">
          {meetings.map((m) => (
            <article
              key={m.id}
              className="card card-hover meeting-card"
            >
              <Link
                href={`/meetings/${m.id}`}
                className="meeting-card-title"
                style={{ display: 'block', textDecoration: 'none' }}
              >
                {m.title || 'Untitled Meeting'}
              </Link>
              <p className="meeting-card-context">{m.meeting_context}</p>
              <div className="meeting-card-meta">
                <span className="meeting-card-date">{formatDate(m.created_at)}</span>
                <StatusBadge status={m.status} />
              </div>
              <div className="meeting-card-actions">
                <Link href={`/meetings/${m.id}`} className="btn-secondary">
                  Open MOM
                </Link>
                {m.status === 'failed' && (
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => handleRetryMeeting(m)}
                    disabled={retryingMeetingId === m.id}
                  >
                    {retryingMeetingId === m.id ? (
                      <>
                        <span className="spin" style={{ width: 14, height: 14 }} />
                        Retrying...
                      </>
                    ) : (
                      <>
                        <RefreshCw size={14} />
                        Retry
                      </>
                    )}
                  </button>
                )}
                <button
                  type="button"
                  className="btn-danger"
                  onClick={() => handleDeleteMeeting(m)}
                  disabled={deletingMeetingId === m.id}
                  aria-label={`Delete ${m.title || 'untitled meeting'}`}
                >
                  {deletingMeetingId === m.id ? (
                    <>
                      <span className="spin" style={{ width: 14, height: 14 }} />
                      Deleting...
                    </>
                  ) : (
                    <>
                      <Trash2 size={14} />
                      Delete
                    </>
                  )}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  )
}
