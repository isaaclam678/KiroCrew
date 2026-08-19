/**
 * Shared file-path context menu: Open in default app, Reveal in Finder / file
 * manager, Copy path. Exposed as a right-click wrapper (`FilePathMenu`) and as
 * standalone context-menu item building blocks (`FilePathMenuItems`).
 *
 * Open/Reveal items render only when `directLocal` is true (the backend reports
 * the request comes from a browser on the same machine). Remote and tunneled
 * sessions see Copy path only, because opening Finder on a host the user is not
 * looking at is useless.
 *
 * The reveal label is platform-aware — it reuses the same gatewayPlatform-driven
 * wording MarkdownPanel's overflow menu uses, so the two menus name the identical
 * action identically ("Open in Finder" on macOS, "Open in File Explorer" on
 * Windows, "Show in file manager" otherwise) instead of drifting apart.
 *
 * "Open with default app" is hidden for directories: `/api/reveal` rejects an
 * `open` action on a directory (400), so offering it would be a guaranteed-fail
 * click. Reveal still applies — it shows the folder in the file manager.
 */
import { type ReactNode } from 'react'
import { ExternalLink, FolderOpen, Copy } from 'lucide-react'
import {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
} from './ui/context-menu'
import { useBranding } from '../hooks/useBranding'
import { useGatewayPlatform } from '../hooks/useGatewayPlatform'
import { api } from '../api/client'
import { copyToClipboard } from '../utils/clipboard'
import { i18nT } from '../i18n/t'

/** What the wrapped path is on disk. Directories cannot be "opened". */
export type FilePathKind = 'file' | 'dir'

async function revealOrOpen(filePath: string, action: 'open' | 'reveal') {
  try {
    await api.revealPath(filePath, action)
  } catch (err) {
    // eslint-disable-next-line no-console -- surface reveal failures for diagnostics
    console.error('revealPath failed', err)
    alert(i18nT('components.filePathMenu.reveal_failed'))
  }
}

// ── Menu-item building blocks ────────────────────────────────────────────────

export interface FilePathMenuItemsProps {
  /** Absolute file path to act on. */
  filePath: string
  /** Override the directLocal flag (defaults to the branding value). Useful
   *  when the consuming component already has the value from another source. */
  directLocal?: boolean
  /** Whether the path is a file or a directory. The Open item is suppressed for
   *  directories, which the reveal endpoint cannot `open`. */
  kind?: FilePathKind
}

/**
 * Renders the file-path action items (Open / Reveal / Copy path) as
 * ContextMenu items. Drop these into any ContextMenuContent.
 */
export function FilePathMenuItems({ filePath, directLocal: directLocalProp, kind }: FilePathMenuItemsProps) {
  const branding = useBranding()
  const isLocal = directLocalProp ?? branding.directLocal
  const gatewayPlatform = useGatewayPlatform()
  // Name the real application where the gateway HAS one, generic otherwise —
  // `/api/reveal` shells out on the gateway, so its platform is the one to name.
  // Same keys as MarkdownPanel's overflow so both surfaces read identically.
  const revealLabel = gatewayPlatform === 'darwin'
    ? i18nT('components.markdownPanel.open_in_finder')
    : gatewayPlatform === 'windows'
      ? i18nT('components.markdownPanel.open_in_file_explorer')
      : i18nT('components.markdownPanel.show_in_file_manager')
  const openLabel = i18nT('components.markdownPanel.open_with_default_app')

  return (
    <>
      {isLocal && kind !== 'dir' && (
        <ContextMenuItem
          onSelect={() => { void revealOrOpen(filePath, 'open') }}
          aria-label={openLabel}
        >
          <ExternalLink size={14} className="lucide-inline" />
          {openLabel}
        </ContextMenuItem>
      )}
      {isLocal && (
        <ContextMenuItem
          onSelect={() => { void revealOrOpen(filePath, 'reveal') }}
          aria-label={revealLabel}
        >
          <FolderOpen size={14} className="lucide-inline" />
          {revealLabel}
        </ContextMenuItem>
      )}
      <ContextMenuItem
        onSelect={() => { copyToClipboard(filePath) }}
        aria-label={i18nT('components.filePathMenu.copy_path')}
      >
        <Copy size={14} className="lucide-inline" />
        {i18nT('components.filePathMenu.copy_path')}
      </ContextMenuItem>
    </>
  )
}

// ── Right-click wrapper ──────────────────────────────────────────────────────

export interface FilePathMenuProps {
  /** Absolute file path to act on. */
  filePath: string
  /** The element that triggers the context menu on right-click. */
  children: ReactNode
  /** Override the directLocal flag. */
  directLocal?: boolean
  /** File or directory — directories hide the Open item (see FilePathMenuItems). */
  kind?: FilePathKind
}

/**
 * Wrap any element to give it a right-click menu with file-path actions.
 *
 * ```tsx
 * <FilePathMenu filePath="/home/user/report.md">
 *   <span className="file-title">report.md</span>
 * </FilePathMenu>
 * ```
 */
export default function FilePathMenu({ filePath, children, directLocal, kind }: FilePathMenuProps) {
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        {children}
      </ContextMenuTrigger>
      <ContextMenuContent className="min-w-[180px]" onClick={e => e.stopPropagation()}>
        <FilePathMenuItems
          filePath={filePath}
          directLocal={directLocal}
          kind={kind}
        />
      </ContextMenuContent>
    </ContextMenu>
  )
}
