'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import toast from 'react-hot-toast'
import { useAuth } from '@/lib/auth'

const registerSchema = z
  .object({
    org_name: z.string().min(2, 'Organisation name must be at least 2 characters'),
    email: z.string().email('Please enter a valid email address'),
    password: z
      .string()
      .min(8, 'Password must be at least 8 characters')
      .regex(/[A-Z]/, 'Must contain an uppercase letter')
      .regex(/[0-9]/, 'Must contain a number'),
    confirm_password: z.string(),
  })
  .refine((d) => d.password === d.confirm_password, {
    message: 'Passwords do not match',
    path: ['confirm_password'],
  })

type RegisterForm = z.infer<typeof registerSchema>

export default function RegisterPage() {
  const router = useRouter()
  const { register: authRegister } = useAuth()
  const [serverError, setServerError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterForm>({ resolver: zodResolver(registerSchema) })

  const onSubmit = async (data: RegisterForm) => {
    setServerError(null)
    try {
      await authRegister(data.org_name, data.email, data.password)
      toast.success('Workspace created! Welcome to Meeting Memory.')
      router.push('/meetings')
    } catch (err: any) {
      setServerError(err.message || 'Registration failed. Please try again.')
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card" style={{ maxWidth: 480 }}>
        {/* Brand */}
        <div className="auth-logo">Meeting Memory</div>
        <p className="auth-tagline">Set up your team&apos;s workspace in seconds</p>

        <h1 className="auth-title">Create your workspace</h1>

        {serverError && (
          <div className="auth-alert" role="alert" style={{ marginBottom: '20px' }}>
            {serverError}
          </div>
        )}

        <form className="auth-form" onSubmit={handleSubmit(onSubmit)} noValidate>
          {/* Organisation Name */}
          <div className="form-group">
            <label htmlFor="reg-org" className="form-label">Organisation name</label>
            <div className="input-wrapper">
              <svg className="input-icon" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M4 4a2 2 0 012-2h8a2 2 0 012 2v12a1 1 0 110 2h-3a1 1 0 01-1-1v-2a1 1 0 00-1-1H9a1 1 0 00-1 1v2a1 1 0 01-1 1H4a1 1 0 110-2V4zm3 1h2v2H7V5zm2 4H7v2h2V9zm2-4h2v2h-2V5zm2 4h-2v2h2V9z" clipRule="evenodd"/>
              </svg>
              <input
                id="reg-org"
                type="text"
                className="form-input"
                placeholder="Acme Corp"
                autoComplete="organization"
                {...register('org_name')}
              />
            </div>
            {errors.org_name && <span className="form-error">{errors.org_name.message}</span>}
          </div>

          {/* Email */}
          <div className="form-group">
            <label htmlFor="reg-email" className="form-label">Work email</label>
            <div className="input-wrapper">
              <svg className="input-icon" viewBox="0 0 20 20" fill="currentColor">
                <path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z"/>
                <path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z"/>
              </svg>
              <input
                id="reg-email"
                type="email"
                className="form-input"
                placeholder="you@company.com"
                autoComplete="email"
                {...register('email')}
              />
            </div>
            {errors.email && <span className="form-error">{errors.email.message}</span>}
          </div>

          {/* Password */}
          <div className="form-group">
            <label htmlFor="reg-password" className="form-label">Password</label>
            <div className="input-wrapper">
              <svg className="input-icon" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clipRule="evenodd"/>
              </svg>
              <input
                id="reg-password"
                type="password"
                className="form-input"
                placeholder="Min. 8 characters, 1 uppercase, 1 number"
                autoComplete="new-password"
                {...register('password')}
              />
            </div>
            {errors.password && <span className="form-error">{errors.password.message}</span>}
          </div>

          {/* Confirm Password */}
          <div className="form-group">
            <label htmlFor="reg-confirm" className="form-label">Confirm password</label>
            <div className="input-wrapper">
              <svg className="input-icon" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clipRule="evenodd"/>
              </svg>
              <input
                id="reg-confirm"
                type="password"
                className="form-input"
                placeholder="Repeat your password"
                autoComplete="new-password"
                {...register('confirm_password')}
              />
            </div>
            {errors.confirm_password && (
              <span className="form-error">{errors.confirm_password.message}</span>
            )}
          </div>

          <button
            id="register-submit-btn"
            type="submit"
            className="btn-primary btn-large"
            disabled={isSubmitting}
            style={{ width: '100%', justifyContent: 'center', marginTop: '8px' }}
          >
            {isSubmitting ? (
              <>
                <span className="spin" style={{ width: 16, height: 16 }} />
                Creating workspace…
              </>
            ) : (
              'Create workspace'
            )}
          </button>
        </form>

        <p className="auth-divider">
          Already have a workspace?{' '}
          <Link href="/login" style={{ fontWeight: 600 }}>
            Sign in →
          </Link>
        </p>
      </div>
    </div>
  )
}
