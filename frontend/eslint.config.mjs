import js from '@eslint/js'
import vitest from '@vitest/eslint-plugin'
import { defineConfig, globalIgnores } from 'eslint/config'
import prettier from 'eslint-config-prettier/flat'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

export default defineConfig([
  globalIgnores(['dist/**', 'coverage/**', '**/*.tsbuildinfo']),
  {
    files: ['**/*.mjs'],
    extends: [js.configs.recommended],
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
    },
  },
  {
    files: ['test/**/*.{ts,tsx}'],
    plugins: { vitest },
    rules: {
      // 非同期APIのstubは、awaitなしでもPromiseを返す契約と例外のrejectを維持する。
      '@typescript-eslint/require-await': 'off',
      // expectへ渡す関数参照を許容し、実際の非束縛呼出しは引き続き検査する。
      '@typescript-eslint/unbound-method': 'off',
      'vitest/unbound-method': 'error',
    },
  },
  {
    files: ['src/**/*.{ts,tsx}', 'test/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
    },
  },
  prettier,
])
