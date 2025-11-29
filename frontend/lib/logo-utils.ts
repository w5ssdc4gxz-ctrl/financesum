const EXCHANGE_SUFFIXES: Record<string, string> = {
  NASDAQ: "US",
  NYSE: "US",
  NYQ: "US",
  NMS: "US",
  AMEX: "US",
  ARCA: "US",
  LSE: "GB",
  LON: "GB",
  TSX: "CA",
  TSXV: "CA",
  TSE: "JP",
  JPX: "JP",
  HKEX: "HK",
  HK: "HK",
  ASX: "AU",
  NSE: "IN",
  BSE: "IN",
  SGX: "SG",
  SIX: "CH",
  FWB: "DE",
  XETRA: "DE",
  SWX: "CH",
  SZSE: "CN",
  SSE: "CN",
  KRX: "KR",
  KOSDAQ: "KR",
  BMV: "MX",
  B3: "BR",
}

const countryOverrides: Record<string, string> = {
  USA: "US",
  UK: "GB",
  UAE: "AE",
  EU: "EU",
}

export const buildEodSymbol = (ticker?: string | null, exchange?: string | null, country?: string | null) => {
  if (!ticker) return null
  const trimmedTicker = ticker.trim().toUpperCase()
  if (!trimmedTicker) return null
  if (trimmedTicker.includes(".")) {
    return trimmedTicker
  }
  const exchangeKey = exchange?.trim().toUpperCase()
  const suffixFromExchange = exchangeKey ? EXCHANGE_SUFFIXES[exchangeKey] : null
  const countryKey = country?.trim().toUpperCase()
  const suffixFromCountry = countryKey ? countryOverrides[countryKey] ?? countryKey.slice(0, 2) : null
  const suffix = suffixFromExchange ?? suffixFromCountry ?? "US"
  return `${trimmedTicker}.${suffix}`
}

export const buildLogoUrl = (ticker?: string | null, exchange?: string | null, country?: string | null) => {
  const symbol = buildEodSymbol(ticker, exchange, country)
  if (!symbol) return null
  return `/api/company-logo?symbol=${encodeURIComponent(symbol)}`
}
