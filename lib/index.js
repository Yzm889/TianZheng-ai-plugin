/**
 * dsh-t20-arch setup plugin.
 *
 * Copies the bundled Python MCP server and launcher into a stable, user-level
 * directory (%LOCALAPPDATA%\dsh-t20-arch), so the MCP row in cordis.patch.yml
 * can reference a fixed path that does not depend on where this npm package is
 * installed, or on the user's name.
 */
import { cpSync, existsSync, mkdirSync, appendFileSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/** Cordis plugin name used by loader diagnostics. */
export const name = 't20-arch-setup'

const pkgDir = dirname(dirname(fileURLToPath(import.meta.url)))
const stateDir = join(process.env.LOCALAPPDATA ?? pkgDir, 'dsh-t20-arch')

function note(message) {
  try {
    mkdirSync(stateDir, { recursive: true })
    appendFileSync(join(stateDir, 'plugin.log'), `${new Date().toISOString()} ${message}\n`)
  } catch {
    // logging must never break plugin loading
  }
}

/**
 * Stage the launcher and Python sources. Clearing the previous copy first keeps
 * upgrades clean (no stale modules from an older version).
 */
export const apply = () => {
  note(`apply start pkgDir=${pkgDir}`)
  try {
    mkdirSync(stateDir, { recursive: true })

    for (const part of ['bin', 'python']) {
      const target = join(stateDir, part)
      try {
        rmSync(target, { recursive: true, force: true })
      } catch (error) {
        note(`could not clear ${target}: ${error?.message ?? error}`)
      }
      cpSync(join(pkgDir, part), target, { recursive: true, force: true })
    }
  } catch (error) {
    note(`staging failed: ${error?.message ?? error}`)
    throw error
  }

  const launcher = join(stateDir, 'bin', 'launch.cmd')
  note(`apply done launcher=${launcher} exists=${existsSync(launcher)}`)
}
