/**
 * Money crosses the UI boundary as decimal MXN text and the API boundary as
 * integer centavos. Keep the two representations separate so a half-edited
 * input is never mistaken for a persisted amount.
 */

const MAX_CENTAVOS = 999_999_999_999n

export class MoneyInputError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'MoneyInputError'
  }
}

function storedCentavos(value: unknown): bigint {
  if (typeof value === 'bigint') return value
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || value < 0) {
      throw new MoneyInputError('Stored amount must be a non-negative safe integer in centavos.')
    }
    return BigInt(value)
  }
  if (typeof value === 'string' && /^\d+$/.test(value)) return BigInt(value)
  throw new MoneyInputError('Stored amount must be an integer in centavos.')
}

/** Convert a complete decimal-MXN input to an integer centavo payload. */
export function mxnToCents(value: string): number {
  if (typeof value !== 'string' || value.length === 0 || value !== value.trim()) {
    throw new MoneyInputError('Enter an amount in MXN, for example 100.00.')
  }
  // Deliberately reject signs, exponents, grouping separators, and a bare
  // decimal point. The input may still hold those temporary strings while
  // editing; submission validates them before constructing an API payload.
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(value)
  if (!match) {
    throw new MoneyInputError('Use MXN decimal notation with a dot and at most two decimals.')
  }
  const fraction = (match[2] ?? '').padEnd(2, '0')
  const cents = BigInt(match[1]) * 100n + BigInt(fraction)
  if (cents > MAX_CENTAVOS || cents > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new MoneyInputError('Amount is outside the supported MXN range.')
  }
  return Number(cents)
}

/** Create the visible draft string from an API centavo amount. */
export function centsToMxn(value: unknown): string {
  const cents = storedCentavos(value)
  if (cents > MAX_CENTAVOS || cents > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new MoneyInputError('Stored amount is outside the supported MXN range.')
  }
  const whole = cents / 100n
  const fraction = (cents % 100n).toString().padStart(2, '0')
  return `${whole.toString()}.${fraction}`
}

export function tryMxnToCents(value: string): { cents?: number; error?: string } {
  try {
    return { cents: mxnToCents(value) }
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Enter a valid MXN amount.' }
  }
}
