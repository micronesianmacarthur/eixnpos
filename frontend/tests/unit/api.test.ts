import { describe, expect, it } from 'vitest'

import { errorMessage } from '../../src/api'

describe('errorMessage', () => {
  it('surfaces a DRF string field error instead of "Request failed (400)"', () => {
    const body = { password: "New password must not be the default or reserved value 'admin'." }
    const message = errorMessage(body, 400)
    expect(message).not.toBe('Request failed (400)')
    expect(message).toMatch(/New password must/)
    expect(message).toMatch(/reserved value/)
  })

  it('surfaces the first message when a field error is an array', () => {
    const body = {
      password: ['New password must be at least 8 characters long and contain both letters and digits.'],
    }
    const message = errorMessage(body, 400)
    expect(message).not.toBe('Request failed (400)')
    expect(message).toMatch(/letters and digits/)
  })

  it('surfaces the first field error regardless of field name (e.g. email)', () => {
    const body = { email: 'Enter a valid email address.' }
    expect(errorMessage(body, 400)).toBe('Enter a valid email address.')
  })

  it('still prefers a top-level detail/error string when present', () => {
    expect(errorMessage({ detail: 'overall failure' }, 400)).toBe('overall failure')
    expect(errorMessage({ error: 'generic' }, 400)).toBe('generic')
  })

  it('still falls back to "Request failed (status)" when no message can be found', () => {
    expect(errorMessage({}, 400)).toBe('Request failed (400)')
    expect(errorMessage({ password: '' }, 400)).toBe('Request failed (400)')
    expect(errorMessage({ password: [] }, 400)).toBe('Request failed (400)')
  })

  it('returns a plain string body directly', () => {
    expect(errorMessage('server down', 500)).toBe('server down')
  })
})
