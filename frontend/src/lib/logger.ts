/**
 * Frontend logger for debugging. In dev or when VITE_DEBUG=true, logs to console with [CursorClone] prefix.
 * Set VITE_DEBUG=true in .env for verbose logs in production builds.
 */

const isDev = import.meta.env.DEV;
const debugEnv = import.meta.env.VITE_DEBUG === 'true' || import.meta.env.VITE_DEBUG === '1';
const enabled = isDev || debugEnv;

const PREFIX = '[CursorClone]';

type Category = 'API' | 'Agent' | 'UI' | 'Repo' | 'File' | 'Diff' | 'App' | 'ChatPanel';

function formatMessage(category: Category, ...args: unknown[]): unknown[] {
  return [`${PREFIX} [${category}]`, ...args];
}

export const logger = {
  debug(category: Category, ...args: unknown[]): void {
    if (enabled) {
      // eslint-disable-next-line no-console
      console.debug(...formatMessage(category, ...args));
    }
  },

  info(category: Category, ...args: unknown[]): void {
    if (enabled) {
      // eslint-disable-next-line no-console
      console.info(...formatMessage(category, ...args));
    }
  },

  warn(category: Category, ...args: unknown[]): void {
    // eslint-disable-next-line no-console
    console.warn(...formatMessage(category, ...args));
  },

  error(category: Category, ...args: unknown[]): void {
    // eslint-disable-next-line no-console
    console.error(...formatMessage(category, ...args));
  },
};
