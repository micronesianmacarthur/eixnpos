import { defineConfig } from 'vitest/config'

// Unit-test config. Tests live in tests/unit and target pure helpers directly
// (no browser/DOM), so we scope the include here and keep them out of the
// Playwright e2e glob ("tests/**/*.spec.ts") and out of the tsc app build.
export default defineConfig({
  test: {
    include: ['tests/unit/**/*.test.ts'],
    environment: 'node',
  },
})
