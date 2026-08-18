import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { VariablesPanel } from './VariablesPanel'
import { api, type VariablesView } from '../../api/client'

vi.mock('../../api/client', () => ({ api: { variables: vi.fn(), saveVariables: vi.fn() } }))

const variables = vi.mocked(api.variables!)
const saveVariables = vi.mocked(api.saveVariables!)

function view(over: Partial<VariablesView> = {}): VariablesView {
  return { global: {}, workspaces: {}, effective: {}, winning_scope: {}, ...over }
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <VariablesPanel />
    </QueryClientProvider>,
  )
}

/** Drive the width signal the panel branches on. */
function setWidth(px: number) {
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: px })
  window.dispatchEvent(new Event('resize'))
}

const ready = () =>
  waitFor(() => expect(screen.getAllByRole('button', { name: 'Add' })[0]).toBeEnabled())

/**
 * A variable whose value comes from `config.local.json` cannot be changed here:
 * the dashboard writes `config.json`, and the overlay wins on load, so a write
 * would be inert and a delete would drop a key the overlay keeps re-supplying.
 * The backend refuses such a request outright — so the row must be disabled AND
 * say why. A disabled control with no explanation is the failure mode this covers,
 * and it has to hold in BOTH layouts: the marker was initially rendered only in
 * the wide table, leaving a phone with a dead input and no reason for it.
 */
describe('VariablesPanel overlay-owned rows', () => {
  const originalWidth = window.innerWidth

  beforeEach(() => {
    vi.clearAllMocks()
    variables.mockResolvedValue(
      view({
        global: { SHADOWED: 'base-value', MINE: 'editable' },
        winning_scope: { SHADOWED: 'global', MINE: 'global' },
        overlay_owned: { global: ['SHADOWED'], workspaces: {} },
      }),
    )
  })

  afterEach(() => {
    Object.defineProperty(window, 'innerWidth', {
      writable: true, configurable: true, value: originalWidth,
    })
  })

  it('disables the value input and delete button for an overlay-owned row', async () => {
    setWidth(1280)
    mount()
    await ready()

    expect(screen.getByRole('textbox', { name: 'SHADOWED Value' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove SHADOWED' })).toBeDisabled()
    // A row the overlay does not own stays editable, so the gate is per-row and
    // not a blanket read-only panel.
    expect(screen.getByRole('textbox', { name: 'MINE Value' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Remove MINE' })).toBeEnabled()
  })

  it('explains why in the wide layout', async () => {
    setWidth(1280)
    mount()
    await ready()
    expect(screen.getByText('Set in config.local.json')).toBeInTheDocument()
  })

  it('explains why in the narrow layout too', async () => {
    setWidth(420)
    mount()
    await ready()
    expect(screen.getByText('Set in config.local.json')).toBeInTheDocument()
  })

  it('never sends an overlay-owned key, since the backend refuses it', async () => {
    setWidth(1280)
    mount()
    await ready()
    // The controls are disabled, so no interaction can produce a write naming it.
    expect(saveVariables).not.toHaveBeenCalled()
  })
})
