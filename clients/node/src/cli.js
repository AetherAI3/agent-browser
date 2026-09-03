#!/usr/bin/env node
/**
 * aether-browser CLI.
 *
 * The runtime is a container you build from source: Agent Browser publishes no
 * Chrome-containing image, so `up` builds one locally the first time and that build
 * takes several minutes. This CLI never pretends otherwise.
 */

import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { homedir, platform } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { AgentBrowser, AgentBrowserError, DEFAULT_BASE_URL } from './index.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const PKG = JSON.parse(readFileSync(join(HERE, '..', 'package.json'), 'utf8'))
const REPO = 'https://github.com/AetherAI3/agent-browser'
const NOVNC_URL = 'http://127.0.0.1:6080/vnc.html'

const styles = { on: process.stdout.isTTY && !process.env.NO_COLOR }
const dim = (s) => (styles.on ? `\u001b[2m${s}\u001b[0m` : s)
const bold = (s) => (styles.on ? `\u001b[1m${s}\u001b[0m` : s)
const red = (s) => (styles.on ? `\u001b[31m${s}\u001b[0m` : s)
const green = (s) => (styles.on ? `\u001b[32m${s}\u001b[0m` : s)
const yellow = (s) => (styles.on ? `\u001b[33m${s}\u001b[0m` : s)

const ok = (s) => console.log(`  ${green('ok')}    ${s}`)
const warn = (s) => console.log(`  ${yellow('warn')}  ${s}`)
const bad = (s) => console.log(`  ${red('fail')}  ${s}`)

function run(cmd, args, options = {}) {
  return spawnSync(cmd, args, { encoding: 'utf8', ...options })
}

function has(cmd) {
  const probe = run(cmd, ['--version'])
  return probe.status === 0 ? (probe.stdout || probe.stderr).trim().split('\n')[0] : undefined
}

function composeCommand() {
  const v2 = run('docker', ['compose', 'version'])
  if (v2.status === 0) return { cmd: 'docker', args: ['compose'], version: v2.stdout.trim() }
  return undefined
}

/** Locate a checkout: the current tree if it is one, otherwise the cached release tarball. */
function findComposeDir({ allowDownload }) {
  let dir = process.cwd()
  for (let depth = 0; depth < 6; depth++) {
    if (existsSync(join(dir, 'docker-compose.yml')) && existsSync(join(dir, 'Dockerfile'))) {
      return { dir, source: 'local checkout' }
    }
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  if (!allowDownload) return undefined

  const version = PKG.version
  const cacheRoot = join(process.env.XDG_CACHE_HOME || join(homedir(), '.cache'), 'aether-browser')
  const target = join(cacheRoot, `agent-browser-${version}`)
  if (existsSync(join(target, 'docker-compose.yml'))) {
    return { dir: target, source: `cached source ${dim(target)}` }
  }

  const url = `${REPO}/archive/refs/tags/v${version}.tar.gz`
  console.log(`  fetching source  ${dim(url)}`)
  mkdirSync(target, { recursive: true })
  const tarball = join(cacheRoot, `v${version}.tar.gz`)
  const curl = run('curl', ['-fsSL', '--proto', '=https', '--tlsv1.2', '-o', tarball, url], {
    stdio: 'inherit',
  })
  if (curl.status !== 0) {
    rmSync(target, { recursive: true, force: true })
    return undefined
  }
  const untar = run('tar', ['-xzf', tarball, '-C', target, '--strip-components=1'], {
    stdio: 'inherit',
  })
  rmSync(tarball, { force: true })
  if (untar.status !== 0) {
    rmSync(target, { recursive: true, force: true })
    return undefined
  }
  return { dir: target, source: `downloaded source ${dim(target)}` }
}

function preflight({ forUp }) {
  const problems = []

  console.log(bold('\nEnvironment'))
  console.log(`  node    ${process.version}`)
  console.log(`  client  aether-browser ${PKG.version}`)
  console.log(`  os      ${platform()}`)

  console.log(bold('\nRuntime prerequisites'))
  if (platform() === 'linux') {
    ok('Linux host')
  } else {
    bad(
      `the documented quickstart is Linux only (found ${platform()}). It relies on Docker host ` +
        `networking so both listeners stay on numeric loopback; Docker Desktop is outside that contract.`,
    )
    problems.push(
      'Run the container on a Linux host. The client library itself works on any platform ' +
        'against a server you can reach.',
    )
  }

  const docker = has('docker')
  if (docker) ok(docker)
  else {
    bad('docker not found on PATH')
    problems.push('Install Docker Engine: https://docs.docker.com/engine/install/')
  }

  const compose = docker ? composeCommand() : undefined
  if (compose) ok(compose.version)
  else if (docker) {
    bad('docker compose v2 not available')
    problems.push('Install the Docker Compose v2 plugin.')
  }

  if (docker) {
    const info = run('docker', ['info', '--format', '{{.ServerVersion}}'])
    if (info.status === 0) ok(`docker daemon reachable (server ${info.stdout.trim()})`)
    else {
      bad('docker daemon is not reachable')
      problems.push('Start Docker, or add your user to the docker group and re-login.')
    }
  }

  if (forUp) {
    console.log(bold('\nSource'))
    const found = findComposeDir({ allowDownload: false })
    if (found) ok(`docker-compose.yml found in ${found.source} ${dim(found.dir)}`)
    else warn(`no checkout here; \`up\` will download ${REPO} at v${PKG.version}`)
  }

  return problems
}

async function probeHealth(baseUrl) {
  const browser = new AgentBrowser({ baseUrl, timeoutMs: 4000 })
  try {
    return { health: await browser.health() }
  } catch (error) {
    return { error }
  }
}

async function cmdDoctor() {
  const problems = preflight({ forUp: true })

  console.log(bold('\nServer'))
  const baseUrl = process.env.AGENT_BROWSER_URL || DEFAULT_BASE_URL
  const { health, error } = await probeHealth(baseUrl)
  if (health) {
    ok(`${baseUrl} responding (version ${health.version})`)
    console.log(
      `        browser_ready=${health.browser_ready} session_active=${health.session_active} ` +
        `slots_available=${health.slots_available}`,
    )
  } else {
    warn(`${baseUrl} not responding yet ${dim(`(${error?.message ?? 'unknown'})`)}`)
    console.log(`        start it with ${bold('npx aether-browser up')}`)
  }

  console.log(bold('\nTokens'))
  if (process.env.AGENT_BROWSER_CONTROLLER_TOKEN) ok('AGENT_BROWSER_CONTROLLER_TOKEN is set')
  else warn('AGENT_BROWSER_CONTROLLER_TOKEN unset (fine for strict loopback local mode)')

  if (problems.length) {
    console.log(bold(red('\nBlocking problems')))
    for (const problem of problems) console.log(`  - ${problem}`)
    console.log()
    return 1
  }
  console.log(green('\nReady.\n'))
  return 0
}

function cmdUp(argv) {
  const problems = preflight({ forUp: false })
  if (problems.length) {
    console.log(bold(red('\nCannot start')))
    for (const problem of problems) console.log(`  - ${problem}`)
    console.log()
    return 1
  }

  console.log(bold('\nSource'))
  const found = findComposeDir({ allowDownload: true })
  if (!found) {
    console.error(red('\nCould not obtain a source checkout to build from.\n'))
    return 1
  }
  ok(found.source)

  console.log(bold('\nBuilding and starting'))
  console.log(
    dim(
      '  The first build installs a hash-locked Python environment and the current\n' +
        '  Google Chrome Stable package, so it can take several minutes.\n',
    ),
  )
  const detach = !argv.includes('--foreground')
  const args = ['compose', 'up', '--build', ...(detach ? ['--detach'] : [])]
  const result = run('docker', args, { cwd: found.dir, stdio: 'inherit' })
  if (result.status !== 0) return result.status ?? 1

  if (detach) {
    console.log(`\n  API     ${DEFAULT_BASE_URL}/browser/health`)
    console.log(`  noVNC   ${NOVNC_URL}`)
    console.log(dim(`\n  Stop with: npx aether-browser down\n`))
  }
  return 0
}

function cmdDown() {
  const found = findComposeDir({ allowDownload: false })
  if (!found) {
    console.error(red('No checkout found here. Run `down` from the directory you ran `up` in.'))
    return 1
  }
  const result = run('docker', ['compose', 'down', '--volumes', '--remove-orphans'], {
    cwd: found.dir,
    stdio: 'inherit',
  })
  return result.status ?? 1
}

async function cmdStatus() {
  const baseUrl = process.env.AGENT_BROWSER_URL || DEFAULT_BASE_URL
  const { health, error } = await probeHealth(baseUrl)
  if (!health) {
    console.error(red(`Agent Browser is not responding at ${baseUrl}`))
    if (error instanceof AgentBrowserError && error.code) console.error(dim(`  ${error.code}`))
    return 1
  }
  console.log(JSON.stringify(health, null, 2))
  return 0
}

function cmdOpen() {
  const opener =
    platform() === 'darwin' ? 'open' : platform() === 'win32' ? 'explorer' : 'xdg-open'
  console.log(`Opening ${NOVNC_URL}`)
  const result = run(opener, [NOVNC_URL], { stdio: 'ignore' })
  if (result.status !== 0) console.log(`Open it manually: ${NOVNC_URL}`)
  return 0
}

async function cmdMcp() {
  // stdout belongs to the JSON-RPC transport from here on; never print to it.
  const { runMcpServer } = await import('./mcp.js')
  runMcpServer()
  return new Promise(() => {})
}

function usage() {
  console.log(`
${bold('aether-browser')} ${dim(PKG.version)}
Client and CLI for Agent Browser by Aether AI.

${bold('Usage')}
  npx aether-browser <command>

${bold('Commands')}
  doctor    Check Docker, platform, ports, and server health, and say what is wrong
  up        Build and start the runtime (Linux + Docker Compose v2; first build is slow)
  down      Stop the runtime and remove its volumes
  status    Print the server health document as JSON
  open      Open the live noVNC view in a browser
  mcp       Serve this session to an MCP client over stdio (Claude Code, Cursor, ...)
  help      Show this message

${bold('Environment')}
  AGENT_BROWSER_URL               Server base URL (default ${DEFAULT_BASE_URL})
  AGENT_BROWSER_CONTROLLER_TOKEN  Controller token, if the server runs authenticated
  AGENT_BROWSER_OBSERVER_TOKEN    Observer token

${bold('MCP')}
  Add to your MCP client config:
  {"mcpServers":{"agent-browser":{"command":"npx","args":["-y","aether-browser","mcp"]}}}

${bold('Library')}
  import { AgentBrowser, withSession } from 'aether-browser'

${dim(REPO)}
`)
  return 0
}

const [, , command = 'help', ...rest] = process.argv
const commands = {
  doctor: cmdDoctor,
  up: () => cmdUp(rest),
  down: cmdDown,
  status: cmdStatus,
  open: cmdOpen,
  mcp: cmdMcp,
  help: usage,
  '--help': usage,
  '-h': usage,
  '--version': () => (console.log(PKG.version), 0),
  '-v': () => (console.log(PKG.version), 0),
}

const handler = commands[command]
if (!handler) {
  console.error(red(`Unknown command: ${command}`))
  usage()
  process.exit(1)
}
process.exit((await handler()) ?? 0)
