// @ts-check

import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config({
  files: ['desktop/**/*.ts', 'owner/**/*.ts', 'admin/**/*.ts'],
  extends: [
    eslint.configs.recommended,
    tseslint.configs.strict,
  ],
});
