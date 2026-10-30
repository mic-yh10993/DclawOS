/*
 * DclawOS logger shim (GPL-2.0-or-later; DclawOS-written).
 * // reuse-check: upstream src/shared/utils/logger.ts NOT copied,
 *   reason: it is built on pino + log-rotation services from the
 *   gateway app; vendored modules in this tree only need the
 *   createLogger(name) surface. This shim provides that surface
 *   over console.error (warn/error) with debug/info suppressed
 *   unless DCLAWOS_DEBUG is set — same call signatures, no pino.
 */
export interface DclawLogger {
  debug: (...args: unknown[]) => void;
  info: (...args: unknown[]) => void;
  warn: (...args: unknown[]) => void;
  error: (...args: unknown[]) => void;
}

export function createLogger(name: string): DclawLogger {
  const debugOn = typeof process !== "undefined" && !!process.env.DCLAWOS_DEBUG;
  const write = (level: string, args: unknown[]) => {
    console.error(`[${name}] ${level}:`, ...args);
  };
  return {
    debug: (...args) => { if (debugOn) write("debug", args); },
    info: (...args) => { if (debugOn) write("info", args); },
    warn: (...args) => write("warn", args),
    error: (...args) => write("error", args),
  };
}

export default createLogger;
