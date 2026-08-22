'use client'

import { useState, useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import toast from 'react-hot-toast'
import { apiClient } from '@/lib/api-client'
import { useAuth } from '@/lib/auth'

const settingsSchema = z.object({
  whatsapp_number: z
    .string()
    .optional()
    .refine(
      (v) => !v || /^whatsapp:\+\d{10,15}$/.test(v),
      'Format: whatsapp:+91XXXXXXXXXX'
    ),
  notify_email: z
    .string()
    .optional()
    .refine((v) => !v || z.string().email().safeParse(v).success, 'Invalid email address'),
})
type SettingsForm = z.infer<typeof settingsSchema>

interface UserProfile {
  id: string
  email: string
  org_id: string
  whatsapp_number: string | null
  notify_email: string | null
}

export default function SettingsPage() {
  const { user, isLoading: authLoading } = useAuth()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [isLoadingProfile, setIsLoadingProfile] = useState(true)

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<SettingsForm>({ resolver: zodResolver(settingsSchema) })

  // Load current profile
  useEffect(() => {
    if (authLoading) return
    ;(async () => {
      try {
        const me: UserProfile = await apiClient.get('/api/v1/users/me')
        setProfile(me)
        reset({
          whatsapp_number: me.whatsapp_number || '',
          notify_email: me.notify_email || '',
        })
      } catch (err: any) {
        toast.error('Could not load profile: ' + err.message)
      } finally {
        setIsLoadingProfile(false)
      }
    })()
  }, [authLoading, reset])

  const onSubmit = async (data: SettingsForm) => {
    try {
      await apiClient.patch('/api/v1/users/me', {
        whatsapp_number: data.whatsapp_number || null,
        notify_email: data.notify_email || null,
      })
      toast.success('Notification preferences saved.')
    } catch (err: any) {
      toast.error(err.message || 'Save failed')
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">Manage your notification preferences.</p>
        </div>
      </div>

      <div style={{ maxWidth: 560 }}>
        {/* Account info card */}
        <div className="card card-body" style={{ marginBottom: 24 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--color-text)', marginBottom: 16 }}>
            Account
          </h2>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 16,
              padding: '12px 16px',
              background: 'rgba(255,255,255,0.03)',
              borderRadius: 10,
              border: '1px solid var(--color-border)',
            }}
          >
            <div
              style={{
                width: 48,
                height: 48,
                borderRadius: '50%',
                background: 'linear-gradient(135deg, var(--color-primary), var(--color-accent))',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 20,
                fontWeight: 700,
                color: '#fff',
                flexShrink: 0,
              }}
            >
              {user?.email?.charAt(0)?.toUpperCase() || '?'}
            </div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--color-text)' }}>
                {user?.email}
              </div>
              <div style={{ fontSize: 12, color: 'var(--color-text-faint)', marginTop: 2 }}>
                Organisation member
              </div>
            </div>
          </div>
        </div>

        {/* Notifications card */}
        <div className="card card-body">
          <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--color-text)', marginBottom: 6 }}>
            Notification Preferences
          </h2>
          <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 24 }}>
            After a MOM is generated, you&apos;ll receive a notification on each enabled
            channel. Leave blank to disable a channel.
          </p>

          {isLoadingProfile ? (
            <div>
              <div className="skeleton skeleton-text" style={{ width: '80%' }} />
              <div className="skeleton skeleton-text" style={{ width: '60%', marginTop: 20 }} />
            </div>
          ) : (
            <form onSubmit={handleSubmit(onSubmit)} noValidate>
              {/* WhatsApp */}
              <div className="form-group" style={{ marginBottom: 24 }}>
                <label htmlFor="whatsapp-number" className="form-label">
                  <span style={{ marginRight: 8 }}>💬</span>WhatsApp number
                </label>
                <input
                  id="whatsapp-number"
                  type="tel"
                  className="form-input"
                  placeholder="whatsapp:+91XXXXXXXXXX"
                  {...register('whatsapp_number')}
                />
                {errors.whatsapp_number ? (
                  <span className="form-error">{errors.whatsapp_number.message}</span>
                ) : (
                  <p className="form-hint">
                    Must be in Twilio format: <code>whatsapp:+91XXXXXXXXXX</code>.
                    The number must have joined the Twilio WhatsApp sandbox.
                  </p>
                )}
              </div>

              {/* Notification Email */}
              <div className="form-group" style={{ marginBottom: 32 }}>
                <label htmlFor="notify-email" className="form-label">
                  <span style={{ marginRight: 8 }}>📧</span>Notification email
                </label>
                <input
                  id="notify-email"
                  type="email"
                  className="form-input"
                  placeholder={profile?.email || 'Override email for MOM notifications'}
                  {...register('notify_email')}
                />
                {errors.notify_email ? (
                  <span className="form-error">{errors.notify_email.message}</span>
                ) : (
                  <p className="form-hint">
                    Leave blank to send to your account email ({profile?.email}).
                  </p>
                )}
              </div>

              <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <button
                  id="save-settings-btn"
                  type="submit"
                  className="btn-primary"
                  disabled={isSubmitting || !isDirty}
                >
                  {isSubmitting ? (
                    <>
                      <span className="spin" style={{ width: 14, height: 14 }} />
                      Saving…
                    </>
                  ) : (
                    'Save preferences'
                  )}
                </button>
                {!isDirty && !isSubmitting && (
                  <span style={{ fontSize: 13, color: 'var(--color-text-faint)' }}>
                    No changes to save
                  </span>
                )}
              </div>
            </form>
          )}
        </div>
      </div>
    </>
  )
}
