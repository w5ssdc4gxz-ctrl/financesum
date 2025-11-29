import countries from "world-countries"

type Coordinates = { lat: number; lng: number }

const centroidMap = new Map<string, Coordinates>()

const aliasOverrides: Record<string, string> = {
  USA: "US",
  "UNITED STATES": "US",
  "UNITED STATES OF AMERICA": "US",
  "U.S.": "US",
  "UK": "GB",
  "U.K.": "GB",
  "GREAT BRITAIN": "GB",
  "SOUTH KOREA": "KR",
  "NORTH KOREA": "KP",
  "SOUTH VIETNAM": "VN",
  "UAE": "AE",
  "UNITED ARAB EMIRATES": "AE",
  "HONG KONG SAR": "HK",
  "CZECH REPUBLIC": "CZ",
  "RUSSIAN FEDERATION": "RU",
}

countries.forEach((country) => {
  const [lat, lng] = country.latlng || []
  if (typeof lat !== "number" || typeof lng !== "number") return
  const record = { lat, lng }
  const register = (key?: string | null) => {
    if (!key) return
    centroidMap.set(key.trim().toUpperCase(), record)
  }
  register(country.cca2)
  register(country.cca3)
  register(country.name.common)
  register(country.name.official)
  if (Array.isArray(country.altSpellings)) {
    country.altSpellings.forEach((value) => register(value))
  }
})

const exchangeCoordinates: Record<string, Coordinates> = {
  NASDAQ: { lat: 40.7549, lng: -73.9840 },
  NYSE: { lat: 40.7069, lng: -74.0113 },
  NYQ: { lat: 40.7069, lng: -74.0113 },
  NMS: { lat: 40.7549, lng: -73.9840 },
  AMEX: { lat: 40.7070, lng: -74.0113 },
  LSE: { lat: 51.5155, lng: -0.0922 },
  LON: { lat: 51.5155, lng: -0.0922 },
  XETRA: { lat: 50.1109, lng: 8.6821 },
  FWB: { lat: 50.1109, lng: 8.6821 },
  TSX: { lat: 43.6487, lng: -79.3817 },
  TSXV: { lat: 43.6487, lng: -79.3817 },
  TSE: { lat: 35.6762, lng: 139.6503 },
  JPX: { lat: 35.6762, lng: 139.6503 },
  HKEX: { lat: 22.2849, lng: 114.1540 },
  ASX: { lat: -33.8651, lng: 151.2099 },
  NSE: { lat: 19.0649, lng: 72.8500 },
  BSE: { lat: 18.9298, lng: 72.8331 },
  SIX: { lat: 47.3769, lng: 8.5417 },
  SGX: { lat: 1.2827, lng: 103.8519 },
  KRX: { lat: 37.5665, lng: 126.9780 },
  KOSDAQ: { lat: 37.5665, lng: 126.9780 },
  BMV: { lat: 19.4326, lng: -99.1332 },
  B3: { lat: -23.5505, lng: -46.6333 },
  JSE: { lat: -26.2041, lng: 28.0473 },
  HK: { lat: 22.2849, lng: 114.1540 },
  SSE: { lat: 31.2304, lng: 121.4737 },
  SZSE: { lat: 22.5431, lng: 114.0579 },
  NZX: { lat: -41.2865, lng: 174.7762 },
  IDX: { lat: -6.2088, lng: 106.8456 },
  KLSE: { lat: 3.1569, lng: 101.7123 },
}

export function getCountryCentroid(country?: string | null): Coordinates | null {
  if (!country) return null
  const normalized = country.trim().toUpperCase()
  const mapped = aliasOverrides[normalized] ?? normalized
  return centroidMap.get(mapped) ?? null
}

export function getCompanyCoordinates(country?: string | null, exchange?: string | null): Coordinates | null {
  const fromCountry = getCountryCentroid(country)
  if (fromCountry) return fromCountry
  if (!exchange) return null
  return exchangeCoordinates[exchange.trim().toUpperCase()] ?? null
}
