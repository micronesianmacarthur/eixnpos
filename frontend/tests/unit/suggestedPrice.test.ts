/**
 * Unit tests for the product form's suggested price calculation.
 *
 * Covers the configured default target margin (including the 15% fallback),
 * the markup formula `unit_cost * (1 + margin / 100)`, two-decimal rounding,
 * and the "no cost, no suggestion" rule.
 */
import { describe, expect, it } from 'vitest'

import {
  DEFAULT_TARGET_MARGIN_PERCENT,
  computeSuggestedPrice,
  resolveTargetMarginPercent,
} from '../../src/suggestedPrice'

describe('resolveTargetMarginPercent', () => {
  it('defaults to 15 percent', () => {
    expect(DEFAULT_TARGET_MARGIN_PERCENT).toBe(15)
  })

  it('keeps valid configured margins, including boundaries', () => {
    expect(resolveTargetMarginPercent(15)).toBe(15)
    expect(resolveTargetMarginPercent(18.5)).toBe(18.5)
    expect(resolveTargetMarginPercent(0)).toBe(0)
    expect(resolveTargetMarginPercent(100)).toBe(100)
  })

  it('accepts a numeric string from the settings API', () => {
    expect(resolveTargetMarginPercent('18.5')).toBe(18.5)
    expect(resolveTargetMarginPercent(' 15 ')).toBe(15)
  })

  it('falls back to 15 when the value is missing', () => {
    expect(resolveTargetMarginPercent(undefined)).toBe(15)
    expect(resolveTargetMarginPercent(null)).toBe(15)
    expect(resolveTargetMarginPercent('')).toBe(15)
    expect(resolveTargetMarginPercent('   ')).toBe(15)
  })

  it('falls back to 15 when the value is not a usable percentage', () => {
    expect(resolveTargetMarginPercent('abc')).toBe(15)
    expect(resolveTargetMarginPercent(Number.NaN)).toBe(15)
    expect(resolveTargetMarginPercent(Number.POSITIVE_INFINITY)).toBe(15)
    expect(resolveTargetMarginPercent(-1)).toBe(15)
    expect(resolveTargetMarginPercent(100.01)).toBe(15)
    expect(resolveTargetMarginPercent(true)).toBe(15)
    expect(resolveTargetMarginPercent({})).toBe(15)
    expect(resolveTargetMarginPercent([])).toBe(15)
  })
})

describe('computeSuggestedPrice', () => {
  it('applies the target margin as a markup on unit cost', () => {
    // Acceptance criterion: margin 15, unit cost 100 => 115.00.
    expect(computeSuggestedPrice('100', 15)).toBe('115.00')
  })

  it('recomputes when the configured margin changes', () => {
    expect(computeSuggestedPrice('100', 10)).toBe('110.00')
    expect(computeSuggestedPrice('100', 25)).toBe('125.00')
    expect(computeSuggestedPrice('100', 0)).toBe('100.00')
  })

  it('rounds to two decimal places (currency minor unit)', () => {
    expect(computeSuggestedPrice('9.25', 15)).toBe('10.64') // 10.6375
    expect(computeSuggestedPrice('12.34', 15)).toBe('14.19') // 14.190999...
    // 1.1 * 1.15 is 1.265 in decimal; binary float noise must not round down.
    expect(computeSuggestedPrice('1.1', 15)).toBe('1.27')
    expect(computeSuggestedPrice('0.05', 15)).toBe('0.06')
  })

  it('returns an empty suggestion when unit cost is empty', () => {
    expect(computeSuggestedPrice('', 15)).toBe('')
    expect(computeSuggestedPrice('   ', 15)).toBe('')
    expect(computeSuggestedPrice(null, 15)).toBe('')
    expect(computeSuggestedPrice(undefined, 15)).toBe('')
  })

  it('returns an empty suggestion for a non-numeric or negative cost', () => {
    expect(computeSuggestedPrice('abc', 15)).toBe('')
    expect(computeSuggestedPrice(Number.NaN, 15)).toBe('')
    expect(computeSuggestedPrice('-5', 15)).toBe('')
  })

  it('calculates a zero cost (zero is a value, not an absence)', () => {
    expect(computeSuggestedPrice('0', 15)).toBe('0.00')
    expect(computeSuggestedPrice(0, 15)).toBe('0.00')
  })

  it('falls back to 15 percent when the margin itself is unusable', () => {
    expect(computeSuggestedPrice('100', Number.NaN)).toBe('115.00')
    expect(computeSuggestedPrice('100', -3)).toBe('115.00')
  })
})
