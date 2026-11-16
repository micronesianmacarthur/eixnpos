import { describe, expect, it } from 'vitest'

import {
  buildUserSaveRequest,
  EMPTY_USER_FORM,
  passwordPolicyError,
  type UserForm,
  type User,
} from '../../src/users'

function createForm(overrides: Partial<UserForm> = {}): UserForm {
  return { ...EMPTY_USER_FORM, ...overrides }
}

const ORIGINAL: User = {
  id: 1,
  email: 'cashier@example.com',
  name: 'Cashier User',
  username: 'cashier',
  first_name: 'Cashier',
  last_name: 'User',
  role: 'cashier',
}

describe('passwordPolicyError', () => {
  it('accepts a valid password (>=8 chars, letter + digit)', () => {
    expect(passwordPolicyError('abc12345')).toBeNull()
  })

  it('rejects a password with only letters (e.g. 8-letter "abcdefgh")', () => {
    const err = passwordPolicyError('abcdefgh')
    expect(err).toMatch(/letters and digits/)
  })

  it('rejects an all-digit password of 8 chars', () => {
    const err = passwordPolicyError('12345678')
    expect(err).toMatch(/letters and digits/)
  })

  it('rejects a too-short password', () => {
    const err = passwordPolicyError('a1')
    expect(err).toMatch(/at least 8 characters/)
  })

  it('rejects the reserved value "admin"', () => {
    const err = passwordPolicyError('admin')
    expect(err).toMatch(/reserved value 'admin'/)
  })

  it('rejects reserved values case- and whitespace-insensitively', () => {
    expect(passwordPolicyError('Admin')).toMatch(/reserved value 'admin'/)
    expect(passwordPolicyError('ADMIN')).toMatch(/reserved value 'admin'/)
    expect(passwordPolicyError(' admin ')).toMatch(/reserved value 'admin'/)
    expect(passwordPolicyError('  ADMIN  ')).toMatch(/reserved value 'admin'/)
  })

  it('leaves an empty password unflagged (means "unchanged" on edit)', () => {
    expect(passwordPolicyError('')).toBeNull()
  })
})

describe('buildUserSaveRequest', () => {
  it('accepts a valid password and includes it in the payload (create)', () => {
    const result = buildUserSaveRequest(
      'create',
      createForm({
        username: 'newuser',
        first_name: 'New',
        password: 'abc12345',
      }),
      null,
    )
    expect(result.error).toBeUndefined()
    expect(result.payload.password).toBe('abc12345')
  })

  it('rejects the reserved value "admin" on create with an inline message', () => {
    const result = buildUserSaveRequest(
      'create',
      createForm({ username: 'newuser', first_name: 'New', password: 'admin' }),
      null,
    )
    expect(result.payload).toEqual({})
    expect(result.error).toMatch(/reserved value 'admin'/)
  })

  it('rejects a case/whitespace variant of the reserved value', () => {
    const result = buildUserSaveRequest(
      'create',
      createForm({ username: 'newuser', first_name: 'New', password: ' Admin ' }),
      null,
    )
    expect(result.payload).toEqual({})
    expect(result.error).toMatch(/reserved value 'admin'/)
  })

  it('rejects an 8-letter-only password (no digit)', () => {
    const result = buildUserSaveRequest(
      'create',
      createForm({ username: 'newuser', first_name: 'New', password: 'abcdefgh' }),
      null,
    )
    expect(result.payload).toEqual({})
    expect(result.error).toMatch(/letters and digits/)
  })

  it('rejects an all-digit password', () => {
    const result = buildUserSaveRequest(
      'create',
      createForm({ username: 'newuser', first_name: 'New', password: '12345678' }),
      null,
    )
    expect(result.payload).toEqual({})
    expect(result.error).toMatch(/letters and digits/)
  })

  it('applies the same policy validation on edit when a password is supplied', () => {
    const weak = buildUserSaveRequest(
      'edit',
      createForm({ email: 'cashier@example.com', password: 'abcdefgh' }),
      ORIGINAL,
    )
    expect(weak.error).toMatch(/letters and digits/)

    const reserved = buildUserSaveRequest(
      'edit',
      createForm({ email: 'cashier@example.com', password: 'admin' }),
      ORIGINAL,
    )
    expect(reserved.error).toMatch(/reserved value 'admin'/)

    const strong = buildUserSaveRequest(
      'edit',
      createForm({ email: 'cashier@example.com', password: 'abc12345' }),
      ORIGINAL,
    )
    expect(strong.error).toBeUndefined()
    expect(strong.payload.password).toBe('abc12345')
  })

  it('does not send a password when the field is left blank on edit', () => {
    const result = buildUserSaveRequest(
      'edit',
      createForm({ email: 'cashier@example.com' }),
      ORIGINAL,
    )
    expect(result.error).toBeUndefined()
    expect(result.payload.password).toBeUndefined()
  })
})
