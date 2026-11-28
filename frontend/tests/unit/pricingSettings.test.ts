/**
 * Unit tests for the Pricing tab's margin parsing/formatting.
 *
 * The client rules mirror the backend's: a number between 0 and 100 inclusive
 * with at most two fraction digits. Trailing zeros are not extra precision
 * (15.500 === 15.5), matching the backend Decimal comparison.
 */
import { describe, expect, it } from 'vitest'

import {
  DEFAULT_TARGET_MARGIN_PERCENT,
  MARGIN_DECIMALS_ERROR,
  MARGIN_RANGE_ERROR,
  formatTargetMarginPercent,
  parseTargetMarginPercent,
  targetMarginFromSettings,
} from '../../src/pricingSettings'

describe('parseTargetMarginPercent', () => {
  it.each([
    ['15', 15],
    ['0', 0],
    ['100', 100],
    ['18.5', 18.5],
    ['15.25', 15.25],
    [' 20 ', 20],
    ['15.500', 15.5],
    ['.5', 0.5],
  ])('accepts %j as %d', (raw, expected) => {
    expect(parseTargetMarginPercent(raw)).toEqual({ ok: true, value: expected })
  })

  it.each(['', '   ', 'abc', '1e2', '0x10', '15%', '-1', '100.01', 'Infinity', 'NaN'])(
    'rejects %j with the range message',
    (raw) => {
      expect(parseTargetMarginPercent(raw)).toEqual({ ok: false, error: MARGIN_RANGE_ERROR })
    },
  )

  it('rejects more than two fraction digits with a precision message', () => {
    expect(parseTargetMarginPercent('15.555')).toEqual({
      ok: false,
      error: MARGIN_DECIMALS_ERROR,
    })
  })
})

describe('targetMarginFromSettings', () => {
  it('reads the configured number', () => {
    expect(targetMarginFromSettings({ default_target_margin_percent: 18.5 })).toBe(18.5)
  })

  it('falls back to 15 when the value is absent or unusable', () => {
    expect(DEFAULT_TARGET_MARGIN_PERCENT).toBe(15)
    expect(targetMarginFromSettings({})).toBe(15)
    expect(targetMarginFromSettings(null)).toBe(15)
    expect(targetMarginFromSettings({ default_target_margin_percent: 'nope' })).toBe(15)
    expect(targetMarginFromSettings({ default_target_margin_percent: 101 })).toBe(15)
    expect(targetMarginFromSettings({ default_target_margin_percent: Number.NaN })).toBe(15)
  })

  it('accepts a numeric string from a lenient server', () => {
    expect(targetMarginFromSettings({ default_target_margin_percent: '20.5' })).toBe(20.5)
  })
})

describe('formatTargetMarginPercent', () => {
  it('renders without trailing zeros', () => {
    expect(formatTargetMarginPercent(15)).toBe('15')
    expect(formatTargetMarginPercent(18.5)).toBe('18.5')
    expect(formatTargetMarginPercent(0)).toBe('0')
  })
})
