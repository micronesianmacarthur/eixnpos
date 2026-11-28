/**
 * Unit tests for the product form's suggested price calculation.
 *
 * Covers the configured default target margin (including the 15% fallback),
 * the gross-margin formula `unit_cost / (1 - margin / 100)`, two-decimal
 * rounding, the unusable-margin rules (including 100%, which would divide by
 * zero), and the "no cost, no suggestion" rule.
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
    // 100 is storable in Settings but unusable here (see the 100% test below),
    // so 99.99 is the highest margin the form can use.
    expect(resolveTargetMarginPercent(99.99)).toBe(99.99)
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

  it('treats the storable 100% margin as unusable', () => {
    // Settings accepts 0-100 inclusive, but a 100% gross margin divides by
    // zero. The form falls back instead of showing Infinity.
    expect(resolveTargetMarginPercent(100)).toBe(15)
    expect(resolveTargetMarginPercent('100')).toBe(15)
  })
})

describe('computeSuggestedPrice', () => {
  it('applies the target margin as a gross margin on the selling price', () => {
    // 100 / (1 - 0.15) = 117.647... -> 117.65.
    expect(computeSuggestedPrice('100', 15)).toBe('117.65')
  })

  it('recomputes when the configured margin changes', () => {
    expect(computeSuggestedPrice('100', 10)).toBe('111.11') // 100 / 0.9
    expect(computeSuggestedPrice('100', 25)).toBe('133.33') // 100 / 0.75
    expect(computeSuggestedPrice('100', 0)).toBe('100.00')
  })

  it('rounds to two decimal places (currency minor unit)', () => {
    expect(computeSuggestedPrice('9.25', 15)).toBe('10.88') // 10.8823...
    expect(computeSuggestedPrice('12.34', 15)).toBe('14.52') // 14.5176...
    // 1.1 / 0.85 = 1.2941...; the float noise must not round it down.
    expect(computeSuggestedPrice('1.1', 15)).toBe('1.29')
    expect(computeSuggestedPrice('0.05', 15)).toBe('0.06') // 0.0588...
  })

  it('rounds a half cent up, even through binary float noise', () => {
    // 0.12 / 0.96 is exactly 0.125 in decimal.
    expect(computeSuggestedPrice('0.12', 4)).toBe('0.13')
    // 2.53 / 0.88 is 2.875 in decimal but 2.8749999999999996 in binary;
    // without the rounding epsilon this would silently become 2.87.
    expect(computeSuggestedPrice('2.53', 12)).toBe('2.88')
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
    expect(computeSuggestedPrice('100', Number.NaN)).toBe('117.65')
    expect(computeSuggestedPrice('100', -3)).toBe('117.65')
  })

  it('never renders Infinity or NaN for a 100% configured margin', () => {
    // 100 / (1 - 100/100) is a division by zero: the value must not reach the
    // read-only field as "Infinity" (or as "NaN" after any arithmetic).
    const values = [100, '100', 100.5, 150].map((margin) =>
      computeSuggestedPrice('100', margin as number),
    )
    for (const value of values) {
      expect(value).toBe('117.65')
      expect(value).not.toMatch(/Infinity|NaN/)
    }
  })

  it('returns an empty suggestion rather than Infinity for an absurd cost', () => {
    // 1e308 / 0.01 overflows a double; the field shows nothing instead of
    // "Infinity".
    expect(computeSuggestedPrice('1e308', 99)).toBe('')
  })
})
