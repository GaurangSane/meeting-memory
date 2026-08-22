import { redirect } from 'next/navigation'

/**
 * Root page — redirect to /meetings (middleware will redirect to /login
 * if the user is not authenticated).
 */
export default function RootPage() {
  redirect('/meetings')
}
