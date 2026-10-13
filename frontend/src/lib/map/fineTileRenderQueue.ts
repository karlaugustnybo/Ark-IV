import type { FineTileChunk, FineTileCommit } from './fineTileCoordinator';

export type ResidentFineTileChunk = FineTileChunk & { sourceKey: string };

/** Warm one tile's GPU buffers per frame, then publish complete coverage.
 * Resident tiles retain their identity across camera generations and reversals.
 */
export class FineTileRenderQueue {
  private readonly resident = new Map<string, ResidentFineTileChunk>();
  private pending: FineTileCommit | null = null;
  private queued: FineTileCommit | null = null;
  private displayed: FineTileCommit | null = null;
  private frame: number | null = null;
  private disposed = false;

  constructor(
    private readonly options: {
      onStage: () => void;
      onPublish: (commit: FineTileCommit) => void;
      requestFrame?: (callback: FrameRequestCallback) => number;
      cancelFrame?: (handle: number) => void;
      maxResidentTiles?: number;
    }
  ) {}

  get chunks(): ResidentFineTileChunk[] {
    return [...this.resident.values()];
  }

  get busy(): boolean {
    return this.pending !== null;
  }

  submit(commit: FineTileCommit): void {
    if (this.disposed) return;
    // Do not let a quickly loaded guard postpone the first visible viewport.
    // A different camera/source supersedes both phases immediately.
    if (
      this.pending &&
      commit.sourceKey === this.pending.sourceKey &&
      commit.chunks.length > this.pending.chunks.length &&
      this.pending.chunks.every((chunk) =>
        commit.chunks.some(
          (candidate) => candidate.key === chunk.key && candidate.cells === chunk.cells
        )
      )
    ) {
      this.queued = commit;
    } else {
      this.pending = commit;
      this.queued = null;
    }
    if (this.frame === null) this.schedule();
  }

  dispose(): void {
    this.disposed = true;
    if (this.frame !== null) (this.options.cancelFrame ?? cancelAnimationFrame)(this.frame);
    this.frame = null;
    this.pending = null;
    this.queued = null;
    this.resident.clear();
  }

  private key(sourceKey: string, chunk: FineTileChunk): string {
    return `${sourceKey}:${chunk.key}`;
  }

  private schedule(): void {
    this.frame = (this.options.requestFrame ?? requestAnimationFrame)(() => this.advance());
  }

  private advance(): void {
    this.frame = null;
    const commit = this.pending;
    if (!commit || this.disposed) return;
    const next = commit.chunks.find(
      (chunk) =>
        chunk.cells.length > 0 &&
        this.resident.get(this.key(commit.sourceKey, chunk))?.cells !== chunk.cells
    );
    if (next) {
      const key = this.key(commit.sourceKey, next);
      this.resident.delete(key);
      this.resident.set(key, { ...next, sourceKey: commit.sourceKey });
      this.prune();
      this.options.onStage();
      // Deck processes this layer before the next queue frame. Even the final
      // tile must finish warming before the visible generation is swapped.
      this.schedule();
      return;
    }
    this.pending = this.queued;
    this.queued = null;
    this.displayed = commit;
    this.prune();
    this.options.onPublish(commit);
    if (this.pending && this.frame === null) this.schedule();
  }

  private prune(): void {
    const limit = this.options.maxResidentTiles ?? 128;
    if (this.resident.size <= limit) return;
    const pinned = new Set<string>();
    for (const commit of [this.displayed, this.pending, this.queued]) {
      if (commit) for (const chunk of commit.chunks) pinned.add(this.key(commit.sourceKey, chunk));
    }
    for (const key of this.resident.keys()) {
      if (this.resident.size <= limit) break;
      if (!pinned.has(key)) this.resident.delete(key);
    }
  }
}
