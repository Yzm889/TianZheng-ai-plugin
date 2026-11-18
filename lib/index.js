/**
 * dsh-t20-arch setup plugin.
 *
 * Copies the bundled Python MCP server and launcher into a stable, user-level
 * directory (%LOCALAPPDATA%\dsh-t20-arch), so the MCP row in cordis.patch.yml
 * can reference a fixed path that does not depend on where this npm package is
 * installed, or on the user's name.
 */
import {
  appendFileSync, copyFileSync, existsSync, mkdirSync, readdirSync, rmSync, statSync,
} from 'node:fs'
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
 * Recursive copy, one file at a time.
 *
 * `fs.cpSync(..., { recursive: true })` kills the node process outright on
 * Windows 11 + node 22 (exit 0xC0000409 STATUS_STACK_BUFFER_OVERRUN) even
 * though copying a single file works fine. Doing the walk ourselves keeps
 * the install working; verified 2026-11-18 by staging bin/ and python/.
 */
function copyTree(src, dst) {
  const st = statSync(src)
  if (st.isDirectory()) {
    mkdirSync(dst, { recursive: true })
    for (const entry of readdirSync(src)) {
      copyTree(join(src, entry), join(dst, entry))
    }
    return
  }
  if (st.isFile()) {
    mkdirSync(dirname(dst), { recursive: true })
    copyFileSync(src, dst)
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
      copyTree(join(pkgDir, part), target)
    }
  } catch (error) {
    note(`staging failed: ${error?.message ?? error}`)
    throw error
  }

  const launcher = join(stateDir, 'bin', 'launch.cmd')
  // Both files are required: launch.cmd is the entry point, run.ps1 does the
  // real work. Missing run.ps1 only surfaced at first launch, long after
  // install looked successful, so check it here instead.
  for (const required of ['launch.cmd', 'run.ps1']) {
    const p = join(stateDir, 'bin', required)
    if (!existsSync(p)) {
      const message = `staging incomplete: bin/${required} missing after copy`
      note(message)
      throw new Error(message)
    }
  }
  note(`apply done launcher=${launcher} exists=${existsSync(launcher)}`)
}
