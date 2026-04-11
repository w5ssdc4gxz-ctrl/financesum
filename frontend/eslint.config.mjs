import nextVitals from 'eslint-config-next/core-web-vitals'

const eslintConfig = [
  ...nextVitals,
  {
    ignores: ['.next/**', '.next-dev/**', 'next-env.d.ts', 'tsconfig.tsbuildinfo'],
  },
  {
    rules: {
      'react-hooks/set-state-in-effect': 'off',
    },
  },
]

export default eslintConfig
