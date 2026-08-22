'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import toast from 'react-hot-toast'
import { RefreshCw, Trash2 } from 'lucide-react'
import { apiClient } from '@/lib/api-client'
import { useAuth } from '@/lib/auth'
import { MomEditor } from '@/components/MomEditor'

interface ActionItem {
  id: string
  task: string
  assignee: string
  deadline: string
  priority: 'High' | 'Medium' | 'Low'
}

interface KeyDecision {
  id: string
  text: string
}

interface MomData {
  summary: string
  key_decisions: KeyDecision[]
  action_items: ActionItem[]
  risks: string[]
  next_steps: string
  version: number
}

interface MomRecord extends MomData {
  id: string
  meeting_id: string
  last_edited_at: string | null
}

interface Meeting {
  id: string
  title: string | null
  meeting_context: string
  status: 'recording' | 'processing' | 'completed' | 'failed'
  created_at: string
  completed_at: string | null
  mom: MomRecord | null
}

function StatusBanner({
  status,
  onRetry,
  isRetrying,
}: {
  status: Meeting['status']
  onRetry: () => void
  isRetrying: boolean
}) {
  if (status === 'processing') {
    return (
      <div className="alert alert-warning" style={{ marginBottom: 32, alignItems: 'center' }}>
        <span className="spin" style={{ flexShrink: 0 }} />
        <div>
          <strong>Generating your MOM…</strong>
          <br />
          The AI is analysing the transcript and applying your team&apos;s memory. This
          usually takes 10–30 seconds. This page refreshes automatically.
        </div>
      </div>
    )
  }
  if (status === 'recording') {
    return (
      <div className="alert alert-info" style={{ marginBottom: 32 }}>
        🎙️ This meeting is still being recorded. MOM generation starts when you stop
        the recording.
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className="alert alert-error failed-meeting-banner" style={{ marginBottom: 32 }}>
        <div>
          <strong>We couldn&apos;t generate a summary.</strong>
          <br />
          This usually means no clear audio was captured during the meeting. Try
          recording again with your microphone closer and unmuted. If this meeting
          has a transcript, you can retry MOM generation.
        </div>
        <button
          type="button"
          className="btn-secondary"
          onClick={onRetry}
          disabled={isRetrying}
        >
          {isRetrying ? (
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
      </div>
    )
  }
  return null
}

const MAX_PROCESSING_POLLS = 60

export default function MeetingDetailPage({
  params,
}: {
  params: { id: string }
}) {
  const router = useRouter()
  const { isLoading: authLoading } = useAuth()
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pollCount, setPollCount] = useState(0)
  const [pollingStopped, setPollingStopped] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [isRetrying, setIsRetrying] = useState(false)
  const pollCountRef = useRef(0)

  const fetchMeeting = useCallback(async () => {
    try {
      const data: Meeting = await apiClient.get(
        `/api/v1/meetings/${params.id}`,
        { redirectOnAuthFailure: false }
      )
      setMeeting(data)
      setError(null)
      setPollingStopped(false)
      return data.status
    } catch (err: any) {
      const message = err.message || 'Failed to load meeting'
      if (message.toLowerCase().includes('authentication expired')) {
        setError('Authentication expired. Please log in again, then reopen this meeting.')
        setPollingStopped(true)
      } else {
        setError(message)
      }
      return null
    } finally {
      setIsLoading(false)
    }
  }, [params.id])

  useEffect(() => {
    if (authLoading) return
    fetchMeeting()
  }, [authLoading, fetchMeeting])

  // Poll every 5 seconds while processing
  useEffect(() => {
    if (authLoading) return
    if (pollingStopped) return
    if (!meeting || meeting.status !== 'processing') return
    const timer = setInterval(async () => {
      const nextPollCount = pollCountRef.current + 1
      pollCountRef.current = nextPollCount
      setPollCount(nextPollCount)

      if (nextPollCount >= MAX_PROCESSING_POLLS) {
        setPollingStopped(true)
        setError(
          'MOM generation is taking longer than expected. Please refresh this page in a minute.'
        )
        clearInterval(timer)
        return
      }

      const status = await fetchMeeting()
      if (status === 'completed' || status === 'failed' || status === null) {
        if (status === null) setPollingStopped(true)
        clearInterval(timer)
      }
    }, 5000)
    return () => clearInterval(timer)
  }, [authLoading, pollingStopped, meeting, fetchMeeting])

  const handleDeleteMeeting = async () => {
    if (!meeting) return
    const title = meeting.title || 'Untitled Meeting'
    const confirmed = window.confirm(
      `Delete "${title}"?\n\nThis will permanently remove the transcript, MOM, and learning embeddings for this meeting.`
    )
    if (!confirmed) return

    setIsDeleting(true)
    try {
      await apiClient.delete(`/api/v1/meetings/${meeting.id}`)
      toast.success('Meeting deleted.')
      router.push('/meetings')
    } catch (err: any) {
      toast.error(err.message || 'Could not delete meeting')
      setIsDeleting(false)
    }
  }

  const handleRetryMeeting = async () => {
    if (!meeting) return
    setIsRetrying(true)
    try {
      const updated: Meeting = await apiClient.post(`/api/v1/meetings/${meeting.id}/retry`, {})
      pollCountRef.current = 0
      setPollCount(0)
      setPollingStopped(false)
      setError(null)
      setMeeting((prev) => (prev ? { ...prev, status: updated.status } : updated))
      toast.success('Retry started. Checking for MOM again.')
    } catch (err: any) {
      toast.error(err.message || 'Could not retry MOM generation')
    } finally {
      setIsRetrying(false)
    }
  }

  if (isLoading) {
    return (
      <div className="loading-spinner">
        <div className="spinner-large" />
        Loading meeting…
      </div>
    )
  }

  if (error || !meeting) {
    const isAuthError = error?.toLowerCase().includes('authentication expired')
    const isTimeoutError = error?.toLowerCase().includes('taking longer than expected')
    return (
      <div className="empty-state">
        <div className="empty-icon">⚠️</div>
        <div className="empty-title">
          {isAuthError
            ? 'Please log in again'
            : isTimeoutError
            ? 'Still generating'
            : 'Meeting not found'}
        </div>
        <p className="empty-description">{error || 'This meeting does not exist or you do not have access.'}</p>
        <Link href={isAuthError ? '/login' : '/meetings'} className="btn-secondary">
          {isAuthError ? 'Go to login' : '← Back to meetings'}
        </Link>
      </div>
    )
  }

  return (
    <>
      {/* Page header */}
      <div className="page-header">
        <div>
          <Link
            href="/meetings"
            style={{ fontSize: 13, color: 'var(--color-text-faint)', display: 'inline-flex', alignItems: 'center', gap: 6, marginBottom: 12 }}
          >
            ← All Meetings
          </Link>
          <h1 className="page-title">{meeting.title || 'Untitled Meeting'}</h1>
          <p className="page-subtitle">{meeting.meeting_context}</p>
        </div>
        <div className="meeting-detail-actions">
          <div style={{ fontSize: 13, color: 'var(--color-text-faint)', textAlign: 'right' }}>
            {new Date(meeting.created_at).toLocaleDateString('en-IN', {
              day: '2-digit', month: 'long', year: 'numeric',
            })}
          </div>
          <button
            type="button"
            className="btn-danger"
            onClick={handleDeleteMeeting}
            disabled={isDeleting}
          >
            {isDeleting ? (
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
      </div>

      {/* Status banner */}
      <StatusBanner
        status={meeting.status}
        onRetry={handleRetryMeeting}
        isRetrying={isRetrying}
      />

      {/* MOM Editor */}
      {meeting.status === 'completed' && meeting.mom ? (
        <MomEditor meetingId={meeting.id} initialMom={meeting.mom} />
      ) : meeting.status === 'processing' ? (
        <div className="card" style={{ padding: 48, textAlign: 'center' }}>
          <div style={{ marginBottom: 16 }}>
            <div className="spinner-large" style={{ margin: '0 auto' }} />
          </div>
          <p style={{ color: 'var(--color-text-muted)', fontSize: 15 }}>
            Checking for MOM… ({pollCount} check{pollCount !== 1 ? 's' : ''})
          </p>
        </div>
      ) : null}
    </>
  )
}
