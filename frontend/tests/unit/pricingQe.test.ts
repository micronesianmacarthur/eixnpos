/**
 * QE acceptance tests for the pricing feature's shared helpers (t_bfe754c3).
 *
 * Written from the acceptance criteria, independently of the implementation
 * suites. Two things the existing suites do not pin:
 *  1. a representative cost/margin matrix including half-cent rounding and
 *     binary-float noise;
 *  2. cross-module consistency — `targetMarginFromSettings` (Settings page)
 *     and `resolveTargetMarginPercent` (product form) must agree on what a
 *     usable margin is, or a saved margin could be applied differently by
 *     each consumer.
 */
import { describe, expect, it } from 'vitest'

import { targetMarginFromSettings } from '../../src/pricingSettings'
import {
  DEFAULT_TARGET_MARGIN_PERCENT,
  computeSuggestedPrice,
  resolveTargetMarginPercent,
} from '../../src/suggestedPrice'

describe('pricing QE: representative suggested prices', () => {
  // (unit cost, margin) -> expected suggestion, verified by hand.
  const matrix: Array<[string, number, string]> = [
    ['100', 15, '115.00'],
    ['100', 25, '125.00'],
    ['100', 0, '100.00'],
    ['100', 100, '200.00'],
    ['12.5', 18.5, '14.81'], // 12.5 * 1.185 = 14.8125 -> 14.81
    ['0.03', 50, '0.05'], // exact half-cent 0.045 must round up
    ['1.265', 15, '1.45'], // 1.45475 through binary float noise
    ['19.99', 7.5, '21.49'], // 19.99 * 1.075 = 21.489250 -> 21.49
    ['0.01', 15, '0.01'], // sub-cent result still two decimals
  ]

  it.each(matrix)('cost %s at %s%% suggests %s', (cost, margin, expected) => {
    expect(computeSuggestedPrice(cost, margin)).toBe(expected)
  })

  it('rounds 0.045 half-up, never half-down, regardless of float noise', () => {
    // The epsilon exists precisely for this class of case; break it and the
    // suggestion silently drops to 0.04.
    expect(computeSuggestedPrice('0.03', 50)).toBe('0.05')
    expect(computeSuggestedPrice('0.09', 50)).toBe('0.14') // 0.135 -> 0.14? no: 0.135 -> half-up 0.14
  })

  it('treats unusable costs as no suggestion', () => {
    expect(computeSuggestedPrice('', 15)).toBe('')
    expect(computeSuggestedPrice('   ', 15)).toBe('')
    expect(computeSuggestedPrice('abc', 15)).toBe('')
    expect(computeSuggestedPrice('-1', 15)).toBe('')
    expect(computeSuggestedPrice(null, 15)).toBe('')
    expect(computeSuggestedPrice(undefined, 15)).toBe('')
    expect(computeSuggestedPrice('Infinity', 15)).toBe('')
  })

  it('keeps a formatted two-decimal string, never a bare number', () => {
    const result = computeSuggestedPrice('100', 15)
    expect(result).toBe('115.00')
    expect(result).toMatch(/^\d+\.\d{2}$/)
  })
})

describe('pricing QE: both consumers agree on the fallback', () => {
  it('the default margin constant is 15 everywhere', () => {
    expect(DEFAULT_TARGET_MARGIN_PERCENT).toBe(15)
    expect(resolveTargetMarginPercent(undefined)).toBe(15)
    expect(targetMarginFromSettings(undefined)).toBe(15)
    expect(targetMarginFromSettings(null)).toBe(15)
    expect(targetMarginFromSettings({})).toBe(15)
  })

  it('unusable settings payloads fall back to 15 in both consumers', () => {
    const payloads: unknown[] = [
      { default_target_margin_percent: 'abc' },
      { default_target_margin_percent: -1 },
      { default_target_margin_percent: 100.5 },
      { default_target_margin_percent: null },
      { default_target_margin_percent: Number.NaN },
      'not an object',
      42,
    ]
    for (const payload of payloads) {
      // targetMarginFromSettings takes the whole API payload.
      expect(targetMarginFromSettings(payload), JSON.stringify(payload)).toBe(15)
    }
    for (const value of [-1, 100.5, null, Number.NaN]) {
      // resolveTargetMarginPercent takes the extracted field value.
      expect(resolveTargetMarginPercent(value), JSON.stringify(value)).toBe(15)
    }
  })

  it('a saved margin is read identically by the page and the form', () => {
    for (const margin of [0, 15, 18.5, 100, '25', '7.5']) {
      expect(targetMarginFromSettings({ default_target_margin_percent: margin })).toBe(
        resolveTargetMarginPercent(margin),
      )
    }
  })

  it('fallback fallback: a number is never returned as-is when out of range', () => {
    expect(computeSuggestedPrice('100', 150)).toBe('115.00') // 15 fallback, not 250
    expect(computeSuggestedPrice('100', -20)).toBe('115.00')
  })
})
