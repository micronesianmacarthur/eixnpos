import { describe, expect, it } from 'vitest'

import {
  formatMoney,
  formatMomDelta,
  formatMomPct,
  momTone,
} from '../../src/dashboard/data'

describe('dashboard format helpers', () => {
  it('formats a 2-dp money string as USD currency', () => {
    expect(formatMoney('1234.56')).toBe('$1,234.56')
    expect(formatMoney('0.00')).toBe('$0.00')
    expect(formatMoney('100.5')).toBe('$100.50')
  })

  it('handles null/undefined/numbers without crashing', () => {
    expect(formatMoney(null)).toBe('$0.00')
    expect(formatMoney(undefined)).toBe('$0.00')
    expect(formatMoney(42)).toBe('$42.00')
  })

  it('renders mom % with sign and 2dp, and em-dash for null', () => {
    expect(formatMomPct(13.16)).toBe('+13.16%')
    expect(formatMomPct(-25.58)).toBe('-25.58%')
    expect(formatMomPct(0)).toBe('0.00%')
    expect(formatMomPct(null)).toBe('—')
  })

  it('renders mom delta signed and absolute-value formatted, em-dash for null', () => {
    expect(formatMomDelta('500.00')).toBe('+$500.00')
    expect(formatMomDelta('-1100.00')).toBe('-$1,100.00')
    expect(formatMomDelta(null)).toBe('—')
  })

  it('classifies mom tone for badges', () => {
    expect(momTone(1.5)).toBe('up')
    expect(momTone(-1.5)).toBe('down')
    expect(momTone(0)).toBe('flat')
    expect(momTone(null)).toBe('flat')
  })
})
