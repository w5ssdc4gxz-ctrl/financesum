// Helper utilities for map data and coordinates

export interface CountryCoordinates {
  [key: string]: [number, number] // [longitude, latitude]
}

// Major country coordinates (capital cities as reference points)
export const countryCoordinates: CountryCoordinates = {
  // North America
  'US': [-95.7129, 37.0902],
  'USA': [-95.7129, 37.0902],
  'United States': [-95.7129, 37.0902],
  'CA': [-106.3468, 56.1304],
  'Canada': [-106.3468, 56.1304],
  'MX': [-102.5528, 23.6345],
  'Mexico': [-102.5528, 23.6345],

  // Europe
  'GB': [-3.4360, 55.3781],
  'UK': [-3.4360, 55.3781],
  'United Kingdom': [-3.4360, 55.3781],
  'DE': [10.4515, 51.1657],
  'Germany': [10.4515, 51.1657],
  'FR': [2.2137, 46.2276],
  'France': [2.2137, 46.2276],
  'IT': [12.5674, 41.8719],
  'Italy': [12.5674, 41.8719],
  'ES': [-3.7492, 40.4637],
  'Spain': [-3.7492, 40.4637],
  'NL': [5.2913, 52.1326],
  'Netherlands': [5.2913, 52.1326],
  'CH': [8.2275, 46.8182],
  'Switzerland': [8.2275, 46.8182],
  'SE': [18.6435, 60.1282],
  'Sweden': [18.6435, 60.1282],
  'NO': [8.4689, 60.4720],
  'Norway': [8.4689, 60.4720],
  'DK': [9.5018, 56.2639],
  'Denmark': [9.5018, 56.2639],
  'PL': [19.1451, 51.9194],
  'Poland': [19.1451, 51.9194],

  // Asia
  'CN': [104.1954, 35.8617],
  'China': [104.1954, 35.8617],
  'JP': [138.2529, 36.2048],
  'Japan': [138.2529, 36.2048],
  'IN': [78.9629, 20.5937],
  'India': [78.9629, 20.5937],
  'KR': [127.7669, 35.9078],
  'South Korea': [127.7669, 35.9078],
  'SG': [103.8198, 1.3521],
  'Singapore': [103.8198, 1.3521],
  'HK': [114.1095, 22.3964],
  'Hong Kong': [114.1095, 22.3964],
  'TW': [120.9605, 23.6978],
  'Taiwan': [120.9605, 23.6978],
  'ID': [113.9213, -0.7893],
  'Indonesia': [113.9213, -0.7893],
  'TH': [100.9925, 15.8700],
  'Thailand': [100.9925, 15.8700],
  'MY': [101.9758, 4.2105],
  'Malaysia': [101.9758, 4.2105],
  'PH': [121.7740, 12.8797],
  'Philippines': [121.7740, 12.8797],
  'VN': [108.2772, 14.0583],
  'Vietnam': [108.2772, 14.0583],

  // Middle East
  'AE': [53.8478, 23.4241],
  'UAE': [53.8478, 23.4241],
  'United Arab Emirates': [53.8478, 23.4241],
  'SA': [45.0792, 23.8859],
  'Saudi Arabia': [45.0792, 23.8859],
  'IL': [34.8516, 31.0461],
  'Israel': [34.8516, 31.0461],
  'TR': [35.2433, 38.9637],
  'Turkey': [35.2433, 38.9637],

  // Oceania
  'AU': [133.7751, -25.2744],
  'Australia': [133.7751, -25.2744],
  'NZ': [174.8860, -40.9006],
  'New Zealand': [174.8860, -40.9006],

  // South America
  'BR': [-51.9253, -14.2350],
  'Brazil': [-51.9253, -14.2350],
  'AR': [-63.6167, -38.4161],
  'Argentina': [-63.6167, -38.4161],
  'CL': [-71.5430, -35.6751],
  'Chile': [-71.5430, -35.6751],
  'CO': [-74.2973, 4.5709],
  'Colombia': [-74.2973, 4.5709],
  'PE': [-75.0152, -9.1900],
  'Peru': [-75.0152, -9.1900],

  // Africa
  'ZA': [22.9375, -30.5595],
  'South Africa': [22.9375, -30.5595],
  'EG': [30.8025, 26.8206],
  'Egypt': [30.8025, 26.8206],
  'NG': [8.6753, 9.0820],
  'Nigeria': [8.6753, 9.0820],
  'KE': [37.9062, -0.0236],
  'Kenya': [37.9062, -0.0236],
}

// Get coordinates for a country name or code
export function getCountryCoordinates(countryIdentifier: string): [number, number] | null {
  if (!countryIdentifier) return null

  // Try direct lookup
  const coords = countryCoordinates[countryIdentifier]
  if (coords) return coords

  // Try case-insensitive lookup
  const lowerIdentifier = countryIdentifier.toLowerCase()
  const entry = Object.entries(countryCoordinates).find(
    ([key]) => key.toLowerCase() === lowerIdentifier
  )

  return entry ? entry[1] : null
}

// Generate demo/sample map data for empty states
export function generateDemoMapData() {
  return [
    {
      name: 'Apple Inc.',
      ticker: 'AAPL',
      country: 'United States',
      coordinates: countryCoordinates['US'],
      value: 5
    },
    {
      name: 'Toyota Motor',
      ticker: 'TM',
      country: 'Japan',
      coordinates: countryCoordinates['JP'],
      value: 3
    },
    {
      name: 'Samsung Electronics',
      ticker: 'SSNLF',
      country: 'South Korea',
      coordinates: countryCoordinates['KR'],
      value: 4
    },
    {
      name: 'Nestlé',
      ticker: 'NSRGY',
      country: 'Switzerland',
      coordinates: countryCoordinates['CH'],
      value: 2
    },
    {
      name: 'Alibaba Group',
      ticker: 'BABA',
      country: 'China',
      coordinates: countryCoordinates['CN'],
      value: 3
    },
    {
      name: 'BHP Group',
      ticker: 'BHP',
      country: 'Australia',
      coordinates: countryCoordinates['AU'],
      value: 2
    },
    {
      name: 'Volkswagen',
      ticker: 'VWAGY',
      country: 'Germany',
      coordinates: countryCoordinates['DE'],
      value: 3
    },
    {
      name: 'BP plc',
      ticker: 'BP',
      country: 'United Kingdom',
      coordinates: countryCoordinates['GB'],
      value: 2
    }
  ]
}
