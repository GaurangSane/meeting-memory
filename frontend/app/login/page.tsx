'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import toast from 'react-hot-toast'
import { useAuth } from '@/lib/auth'

const loginSchema = z.object({
  email: z.string().email('Please enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
})
type LoginForm = z.infer<typeof loginSchema>

export default function LoginPage() {
  const router = useRouter()
  const { login } = useAuth()
  const [serverError, setServerError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginForm>({ resolver: zodResolver(loginSchema) })

  const onSubmit = async (data: LoginForm) => {
    setServerError(null)
    try {
      await login(data.email, data.password)
      toast.success('Welcome back!')
      
      // Force a full browser navigation
      window.location.href = '/meetings' 
    } catch (err: any) {
      setServerError(err.message || 'Login failed. Please check your credentials.')
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        {/* Brand */}
        <div className="auth-logo">Meeting Memory</div>
        <p className="auth-tagline">
          AI-powered minutes that get smarter with every meeting
        </p>

        <h1 className="auth-title">Sign in to your workspace</h1>

        {serverError && (
          <div className="auth-alert" role="alert" style={{ marginBottom: '20px' }}>
            {serverError}
          </div>
        )}

        <form className="auth-form" onSubmit={handleSubmit(onSubmit)} noValidate>
          {/* Email */}
          <div className="form-group">
            <label htmlFor="login-email" className="form-label">Email address</label>
            <div className="input-wrapper">
              <svg className="input-icon" viewBox="0 0 20 20" fill="currentColor">
                <path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z"/>
                <path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z"/>
              </svg>
              <input
                id="login-email"
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
            <label htmlFor="login-password" className="form-label">Password</label>
            <div className="input-wrapper">
              <svg className="input-icon" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clipRule="evenodd"/>
              </svg>
              <input
                id="login-password"
                type="password"
                className="form-input"
                placeholder="••••••••"
                autoComplete="current-password"
                {...register('password')}
              />
            </div>
            {errors.password && <span className="form-error">{errors.password.message}</span>}
          </div>

          <button
            id="login-submit-btn"
            type="submit"
            className="btn-primary btn-large"
            disabled={isSubmitting}
            style={{ width: '100%', justifyContent: 'center', marginTop: '8px' }}
          >
            {isSubmitting ? (
              <>
                <span className="spin" style={{ width: 16, height: 16 }} />
                Signing in…
              </>
            ) : (
              'Sign in'
            )}
          </button>
        </form>

        <p className="auth-divider">
          Don&apos;t have an account?{' '}
          <Link href="/register" style={{ fontWeight: 600 }}>
            Create your workspace →
          </Link>
        </p>
      </div>
    </div>
  )
}
