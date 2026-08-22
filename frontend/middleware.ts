import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export function middleware(request: NextRequest) {
  const session = request.cookies.get('refresh_token')
  const { pathname } = request.nextUrl

  const isAuthPage =
    pathname.startsWith('/login') || pathname.startsWith('/register')
  const isPublic =
    pathname.startsWith('/_next') ||
    pathname.startsWith('/favicon') ||
    pathname.startsWith('/api')

  if (isPublic) return NextResponse.next()

  if (!session && !isAuthPage) {
    const url = request.nextUrl.clone()
    url.pathname = '/login'
    return NextResponse.redirect(url)
  }

  if (session && isAuthPage) {
    const url = request.nextUrl.clone()
    url.pathname = '/meetings'
    return NextResponse.redirect(url)
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
