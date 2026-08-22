/**
 * components/MomEditor.tsx
 *
 * Interactive MOM editor — Phase 10, Step 10.1 (PLAN_WEB_SAAS.md).
 *
 * This is the FRONTEND HALF of the RAG correction learning loop.
 *
 * When a user edits a field and clicks Save:
 *   1. The full updated MomData is sent via PATCH /api/v1/meetings/{id}/mom.
 *   2. The backend diffs the payload against the stored MOM, field by field.
 *   3. Each diff is written to mom_edit_history and embedded as a
 *      'correction' in meeting_embeddings (via embed_meeting_task).
 *   4. The response includes `corrections_captured` — surfaced to the user
 *      in the toast so the "system is learning" promise is visible and
 *      verifiable, not an invisible backend detail.
 *
 * CRITICAL: The PATCH endpoint is the ONLY place corrections are captured.
 * Do not bypass it with direct writes to mom_records.
 *
 * Editor design:
 *   - Tabbed interface: Summary | Decisions | Actions | Risks | Next Steps
 *   - In-place editing: textareas for prose, table rows for action items
 *   - Action item priority: colour-coded dropdown (High/Medium/Low)
 *   - Version badge updated on save
 *   - Cancel resets to initialMom without a server call
 */

'use client'

import { useState, useCallback } from 'react'
import toast from 'react-hot-toast'
import { apiClient } from '@/lib/api-client'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ActionItem {
  id: string
  task: string
  assignee: string
  deadline: string
  priority: 'High' | 'Medium' | 'Low'
}

export interface KeyDecision {
  id: string
  text: string
}

export interface MomData {
  summary: string
  key_decisions: KeyDecision[]
  action_items: ActionItem[]
  risks: string[]
  next_steps: string
  version: number
}

interface Props {
  meetingId: string
  initialMom: MomData
}

type Tab = 'summary' | 'decisions' | 'actions' | 'risks' | 'next_steps'

const TABS: { id: Tab; label: string; emoji: string }[] = [
  { id: 'summary',    label: 'Summary',    emoji: '📋' },
  { id: 'decisions',  label: 'Decisions',  emoji: '✅' },
  { id: 'actions',    label: 'Actions',    emoji: '⚡' },
  { id: 'risks',      label: 'Risks',      emoji: '⚠️' },
  { id: 'next_steps', label: 'Next Steps', emoji: '🗓️' },
]

const PRIORITY_COLORS: Record<string, string> = {
  High:   'priority-high',
  Medium: 'priority-medium',
  Low:    'priority-low',
}

// ── Component ─────────────────────────────────────────────────────────────────

export function MomEditor({ meetingId, initialMom }: Props) {
  const [mom, setMom] = useState<MomData>(initialMom)
  const [activeTab, setActiveTab] = useState<Tab>('summary')
  const [isSaving, setIsSaving] = useState(false)
  const [isDirty, setIsDirty] = useState(false)

  // ── Field updaters ──────────────────────────────────────────────────────────

  const updateField = useCallback(
    <K extends keyof MomData>(field: K, value: MomData[K]) => {
      setMom((prev) => ({ ...prev, [field]: value }))
      setIsDirty(true)
    },
    []
  )

  const updateActionItem = useCallback(
    (id: string, key: keyof ActionItem, value: string) => {
      setMom((prev) => ({
        ...prev,
        action_items: prev.action_items.map((item) =>
          item.id === id ? { ...item, [key]: value } : item
        ),
      }))
      setIsDirty(true)
    },
    []
  )

  const updateDecision = useCallback((id: string, text: string) => {
    setMom((prev) => ({
      ...prev,
      key_decisions: prev.key_decisions.map((d) =>
        d.id === id ? { ...d, text } : d
      ),
    }))
    setIsDirty(true)
  }, [])

  const updateRisk = useCallback((index: number, value: string) => {
    setMom((prev) => {
      const risks = [...prev.risks]
      risks[index] = value
      return { ...prev, risks }
    })
    setIsDirty(true)
  }, [])

  // ── Save handler — the learning loop capture point ─────────────────────────
  // This matches the critical submit logic in PLAN_WEB_SAAS.md §10.1 exactly.

  const handleSave = useCallback(async () => {
    setIsSaving(true)
    try {
      const res = await apiClient.patch(`/api/v1/meetings/${meetingId}/mom`, {
        summary:       mom.summary,
        key_decisions: mom.key_decisions,
        action_items:  mom.action_items,
        risks:         mom.risks,
        next_steps:    mom.next_steps,
      })

      // Update version from server response
      setMom((prev) => ({ ...prev, version: res.version ?? prev.version + 1 }))
      setIsDirty(false)

      // Surface the correction count — makes the "system is learning" visible.
      if (res.corrections_captured > 0) {
        toast.success(
          `Saved. ${res.corrections_captured} correction(s) recorded — future meetings will use this.`,
          { duration: 5000, icon: '🧠' }
        )
      } else {
        toast.success('Saved.')
      }
    } catch (err: any) {
      toast.error(err.message || 'Save failed. Please try again.')
    } finally {
      setIsSaving(false)
    }
  }, [meetingId, mom])

  // ── Cancel handler ─────────────────────────────────────────────────────────

  const handleCancel = useCallback(() => {
    setMom(initialMom)
    setIsDirty(false)
    toast('Changes discarded.', { icon: '↩️' })
  }, [initialMom])

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="card">
      {/* Header */}
      <div
        className="card-header"
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-text)', margin: 0 }}>
            Minutes of Meeting
          </h2>
          <span className="version-badge">v{mom.version}</span>
          {isDirty && (
            <span style={{ fontSize: 12, color: 'var(--color-warning)', fontWeight: 500 }}>
              • Unsaved changes
            </span>
          )}
        </div>

        <div style={{ display: 'flex', gap: 10 }}>
          <button
            id="mom-cancel-btn"
            onClick={handleCancel}
            className="btn-secondary"
            disabled={!isDirty || isSaving}
          >
            Cancel
          </button>
          <button
            id="mom-save-btn"
            onClick={handleSave}
            className="btn-primary"
            disabled={!isDirty || isSaving}
          >
            {isSaving ? (
              <>
                <span className="spin" style={{ width: 14, height: 14 }} />
                Saving…
              </>
            ) : (
              '💾 Save MOM'
            )}
          </button>
        </div>
      </div>

      <div className="card-body">
        {/* Tab navigation */}
        <nav className="tab-nav" aria-label="MOM sections">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              id={`tab-${tab.id}`}
              className={`tab-btn${activeTab === tab.id ? ' tab-btn-active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
              aria-selected={activeTab === tab.id}
              role="tab"
            >
              {tab.emoji} {tab.label}
              {/* Count badges */}
              {tab.id === 'decisions' && mom.key_decisions.length > 0 && (
                <span
                  style={{
                    marginLeft: 6,
                    background: 'rgba(99,102,241,0.15)',
                    color: '#a5b4fc',
                    borderRadius: 99,
                    padding: '1px 7px',
                    fontSize: 11,
                    fontWeight: 700,
                  }}
                >
                  {mom.key_decisions.length}
                </span>
              )}
              {tab.id === 'actions' && mom.action_items.length > 0 && (
                <span
                  style={{
                    marginLeft: 6,
                    background: 'rgba(99,102,241,0.15)',
                    color: '#a5b4fc',
                    borderRadius: 99,
                    padding: '1px 7px',
                    fontSize: 11,
                    fontWeight: 700,
                  }}
                >
                  {mom.action_items.length}
                </span>
              )}
            </button>
          ))}
        </nav>

        {/* ── Tab: Summary ──────────────────────────────────────────────────── */}
        {activeTab === 'summary' && (
          <div role="tabpanel" aria-labelledby="tab-summary">
            <div className="form-group">
              <label htmlFor="summary-textarea" className="form-label">
                Executive Summary
              </label>
              <textarea
                id="summary-textarea"
                className="form-textarea"
                style={{ minHeight: 180 }}
                value={mom.summary || ''}
                onChange={(e) => updateField('summary', e.target.value)}
                placeholder="A concise summary of what was discussed and decided…"
              />
            </div>
          </div>
        )}

        {/* ── Tab: Key Decisions ────────────────────────────────────────────── */}
        {activeTab === 'decisions' && (
          <div role="tabpanel" aria-labelledby="tab-decisions">
            <p className="form-hint" style={{ marginBottom: 16 }}>
              Each key decision that was made during the meeting. Edit any decision
              — changes are captured as corrections to teach future generation.
            </p>
            {mom.key_decisions.length === 0 ? (
              <div
                style={{
                  color: 'var(--color-text-faint)',
                  fontStyle: 'italic',
                  padding: '16px 0',
                  fontSize: 14,
                }}
              >
                No key decisions were extracted from this meeting.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {mom.key_decisions.map((d, i) => (
                  <div key={d.id} className="list-edit-item">
                    <span
                      style={{
                        flexShrink: 0,
                        width: 26,
                        height: 26,
                        background: 'rgba(99,102,241,0.15)',
                        borderRadius: '50%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 12,
                        fontWeight: 700,
                        color: '#a5b4fc',
                        marginTop: 8,
                      }}
                    >
                      {i + 1}
                    </span>
                    <input
                      id={`decision-${d.id}`}
                      type="text"
                      className="form-input"
                      value={d.text}
                      onChange={(e) => updateDecision(d.id, e.target.value)}
                      placeholder="Decision text…"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Tab: Action Items ─────────────────────────────────────────────── */}
        {activeTab === 'actions' && (
          <div role="tabpanel" aria-labelledby="tab-actions">
            <p className="form-hint" style={{ marginBottom: 16 }}>
              Edit assignees, deadlines, and priorities. Every change is recorded
              and fed back to future meetings — this is how the AI learns your
              team&apos;s correct patterns.
            </p>
            {mom.action_items.length === 0 ? (
              <div
                style={{
                  color: 'var(--color-text-faint)',
                  fontStyle: 'italic',
                  padding: '16px 0',
                  fontSize: 14,
                }}
              >
                No action items were extracted from this meeting.
              </div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table className="action-items-table">
                  <thead>
                    <tr>
                      <th style={{ minWidth: 200 }}>Task</th>
                      <th style={{ minWidth: 130 }}>Assignee</th>
                      <th style={{ minWidth: 110 }}>Deadline</th>
                      <th style={{ minWidth: 110 }}>Priority</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mom.action_items.map((item) => (
                      <tr key={item.id}>
                        {/* Task */}
                        <td>
                          <input
                            id={`action-task-${item.id}`}
                            type="text"
                            className="form-input"
                            style={{ fontSize: 13 }}
                            value={item.task}
                            onChange={(e) =>
                              updateActionItem(item.id, 'task', e.target.value)
                            }
                          />
                        </td>
                        {/* Assignee */}
                        <td>
                          <input
                            id={`action-assignee-${item.id}`}
                            type="text"
                            className="form-input"
                            style={{ fontSize: 13 }}
                            value={item.assignee}
                            onChange={(e) =>
                              updateActionItem(item.id, 'assignee', e.target.value)
                            }
                          />
                        </td>
                        {/* Deadline */}
                        <td>
                          <input
                            id={`action-deadline-${item.id}`}
                            type="text"
                            className="form-input"
                            style={{ fontSize: 13 }}
                            value={item.deadline}
                            onChange={(e) =>
                              updateActionItem(item.id, 'deadline', e.target.value)
                            }
                            placeholder="e.g. 2024-07-15"
                          />
                        </td>
                        {/* Priority */}
                        <td>
                          <select
                            id={`action-priority-${item.id}`}
                            className={`form-select ${PRIORITY_COLORS[item.priority] || ''}`}
                            style={{ fontSize: 13, fontWeight: 600 }}
                            value={item.priority}
                            onChange={(e) =>
                              updateActionItem(
                                item.id,
                                'priority',
                                e.target.value as 'High' | 'Medium' | 'Low'
                              )
                            }
                          >
                            <option value="High">🔴 High</option>
                            <option value="Medium">🟡 Medium</option>
                            <option value="Low">🟢 Low</option>
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ── Tab: Risks ────────────────────────────────────────────────────── */}
        {activeTab === 'risks' && (
          <div role="tabpanel" aria-labelledby="tab-risks">
            <p className="form-hint" style={{ marginBottom: 16 }}>
              Risks and concerns raised during the meeting.
            </p>
            {mom.risks.length === 0 ? (
              <div
                style={{
                  color: 'var(--color-text-faint)',
                  fontStyle: 'italic',
                  padding: '16px 0',
                  fontSize: 14,
                }}
              >
                No risks were identified in this meeting.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {mom.risks.map((risk, i) => (
                  <div key={i} className="list-edit-item">
                    <span
                      style={{
                        flexShrink: 0,
                        fontSize: 16,
                        marginTop: 10,
                      }}
                    >
                      ⚠️
                    </span>
                    <input
                      id={`risk-${i}`}
                      type="text"
                      className="form-input"
                      value={risk}
                      onChange={(e) => updateRisk(i, e.target.value)}
                      placeholder="Risk description…"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Tab: Next Steps ───────────────────────────────────────────────── */}
        {activeTab === 'next_steps' && (
          <div role="tabpanel" aria-labelledby="tab-next_steps">
            <div className="form-group">
              <label htmlFor="next-steps-textarea" className="form-label">
                Next Steps
              </label>
              <textarea
                id="next-steps-textarea"
                className="form-textarea"
                style={{ minHeight: 140 }}
                value={mom.next_steps || ''}
                onChange={(e) => updateField('next_steps', e.target.value)}
                placeholder="What happens after this meeting? Key follow-ups, dates, owners…"
              />
            </div>
          </div>
        )}

        {/* Bottom action bar */}
        {isDirty && (
          <div
            style={{
              marginTop: 24,
              paddingTop: 20,
              borderTop: '1px solid var(--color-border)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: 12,
            }}
          >
            <div className="alert alert-info" style={{ flex: 1, marginBottom: 0 }}>
              💡 Saving edits teaches the AI your team&apos;s patterns. Corrected
              assignees, priorities, or text will be surfaced in future meeting
              generation automatically.
            </div>
            <div style={{ display: 'flex', gap: 10, flexShrink: 0 }}>
              <button
                onClick={handleCancel}
                className="btn-secondary"
                disabled={isSaving}
              >
                Discard
              </button>
              <button
                onClick={handleSave}
                className="btn-primary"
                disabled={isSaving}
              >
                {isSaving ? (
                  <>
                    <span className="spin" style={{ width: 14, height: 14 }} />
                    Saving…
                  </>
                ) : (
                  '💾 Save & Record Corrections'
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
