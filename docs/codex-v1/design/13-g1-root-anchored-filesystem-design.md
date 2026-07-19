# G1.4b3 root-anchored filesystem safety design

Status: implementation-ready, read-only review. Scope is limited to the two
remaining Important findings: rollback postimage CAS and pathname check/use
races in `workspace_apply.py` / `git_backend.py`.

## Finding and root cause

1. `WorkspaceApplyEngine` validates a `Path`, then later resolves the pathname
   again for `read_bytes`, `unlink`, `rmdir`, `mkdir`, and `os.replace`. A parent
   can be renamed/replaced with a symlink between those calls, so the checked
   directory is not necessarily the used directory.
2. `_matches_backup_entry()` and `GitBackend.read_worktree_entry()` do
   `lstat()` followed by a pathname read. A leaf swap can make the read follow a
   different object (including a symlink) than the object that was classified.
3. `restore()` removes every affected current path before restoring the backup.
   There is no record of the exact postimage owned by this apply, so an external
   edit made after materialization can be destroyed before drift is reported.
4. The current `mutation_started: bool` proves only that some mutation was
   attempted; it is not sufficient ownership evidence for rollback.

## Minimal architecture: one filesystem authority

Add one low-level module, `tianshu.executor.rooted_fs`. Both
`WorkspaceApplyEngine` and `GitBackend.read_worktree_entry()` must delegate to
it. Do not keep a `Path` implementation beside the fd implementation.

Normalize a canonical relative path once, before entering this module. The
module accepts a value object / tuple of components; it does not reinterpret
policy. All reads and mutations below an opened root use only its fd and
relative component names. `Path.resolve/exists/is_*`, `Path.read_bytes`, and
absolute-path mutation must disappear from governed apply.

Suggested interfaces:

```python
@dataclass(frozen=True)
class RelativePath:
    value: str
    parts: tuple[str, ...]       # already policy-validated, never empty

@dataclass(frozen=True)
class StatIdentity:
    kind: Literal["missing", "file", "symlink", "directory"]
    mode: int | None = None
    dev: int | None = None
    ino: int | None = None
    size: int | None = None
    mtime_ns: int | None = None
    ctime_ns: int | None = None

@dataclass(frozen=True)
class EntryImage:
    stat: StatIdentity
    digest: str | None = None    # SHA-256 of regular/link bytes
    content: bytes | None = None # populated for backup / Git entry reads

RaceHook = Callable[[str, RelativePath], None]

class RootedDirectory:
    @classmethod
    def open(
        cls, root: Path, *, race_hook: RaceHook | None = None
    ) -> "RootedDirectory": ...

    def close(self) -> None: ...
    def capture(
        self, path: RelativePath, *, include_content: bool, byte_limit: int
    ) -> EntryImage: ...
    def ensure_parents(
        self, path: RelativePath, *, mode: int = 0o755
    ) -> tuple["OwnedDirectory", ...]: ...
    def remove_leaf(self, path: RelativePath, *, expected: EntryImage) -> EntryImage: ...
    def replace_regular(
        self, path: RelativePath, content: bytes, mode: int
    ) -> EntryImage: ...
    def replace_symlink(self, path: RelativePath, target: bytes) -> EntryImage: ...
    def restore(
        self, record: "MutationRecord"
    ) -> Literal["restored", "already_preimage"]: ...
    def assert_published_root(self) -> None: ...

@dataclass(frozen=True)
class OwnedDirectory:
    path: RelativePath
    identity: StatIdentity

@dataclass
class MutationRecord:
    path: RelativePath
    preimage: EntryImage
    intended: EntryImage                 # logical expected postimage
    state: Literal["planned", "pending", "applied", "restored"]
    owned_postimage: EntryImage | None = None

class ApplyFsSession:
    source: RootedDirectory
    staging: RootedDirectory
    journal: list[MutationRecord]
    created_directories: list[OwnedDirectory]
    def close(self) -> None: ...

class WorkspaceApplyEngine:
    def open_session(
        self, source: GitLocation, staging: GitLocation
    ) -> ApplyFsSession: ...
    def preflight(
        self, session: ApplyFsSession, changes: tuple[CanonicalChange, ...]
    ) -> ApplyPlan: ...
    def backup(self, session: ApplyFsSession, plan: ApplyPlan) -> SourceBackup: ...
    def materialize(
        self, session: ApplyFsSession, backup: SourceBackup, plan: ApplyPlan
    ) -> None: ...
    def verify_materialized(
        self, session: ApplyFsSession, source: GitLocation, plan: ApplyPlan
    ) -> None: ...
    def restore(self, session: ApplyFsSession, backup: SourceBackup) -> None: ...
```

`workspace_service._apply_locked()` opens the session once before preflight and
closes it in one unconditional `finally`. It may retain the fds across awaits;
fds are process-wide, and apply already serializes a source. No other method may
open a second root for governed source/staging content.

For compatibility, `GitBackend.read_worktree_entry(location, path)` may be a
small wrapper that opens/closes a `RootedDirectory`. Governed apply should call
an overload accepting the existing session root:

```python
def read_worktree_entry(
    self,
    location: GitLocation,
    relative_path: str,
    *,
    rooted: RootedDirectory | None = None,
) -> GitWorktreeEntry: ...
```

## Root and parent traversal

Open the root with:

```python
ROOT_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
before = os.lstat(root)
root_fd = os.open(root, ROOT_FLAGS)
after = os.fstat(root_fd)
require real_directory(before) and same_dev_ino_mode(before, after)
```

Store the opened root's `(dev, ino, mode)` and original published pathname.
`assert_published_root()` is detection only: `lstat/open/fstat` the published
root and compare it with the pinned root. It must never become a mutation
authority.

For each parent component, starting with `os.dup(root_fd)`:

```python
child_fd = os.open(
    component,
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    dir_fd=parent_fd,
)
child_stat = os.fstat(child_fd)
require stat.S_ISDIR(child_stat.st_mode)
```

Close traversal fds deterministically. If a missing parent is allowed, call
`os.mkdir(component, mode, dir_fd=parent_fd)`, immediately open it as above,
then record the opened `(dev, ino, mode)` as `OwnedDirectory`. A later rename or
symlink at the published component cannot redirect operations through the
already-open fd.

Do not recursively remove directories. Only `os.rmdir(name, dir_fd=parent_fd)`
is allowed, after exact identity/ownership validation; non-empty means safe
failure.

## Exact leaf reads (fixes `lstat/read_bytes`)

Regular file read:

```python
fd = os.open(
    leaf,
    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
    dir_fd=parent_fd,
)
before = os.fstat(fd)
require stat.S_ISREG(before.st_mode) and before.st_size <= limit
content = bounded_os_read_loop(fd, limit)
after = os.fstat(fd)
require same(dev, ino, mode, size, mtime_ns, ctime_ns, before, after)
```

The opened fd, not the earlier pathname classification, is authoritative.
This must replace both `target.read_bytes()` sites in scope.

For a symlink, macOS exposes `O_SYMLINK` but not Linux `O_PATH`, and Python has
no portable `readlink`-from-fd operation. Use the pinned parent fd with
`stat(follow_symlinks=False) -> readlink(dir_fd=...) -> stat(...)`, comparing
`dev/ino/mode/size/mtime/ctime` before and after. On macOS, opening an additional
`O_RDONLY | O_SYMLINK | O_CLOEXEC` fd and comparing its `fstat` identity further
narrows the window, but the target still comes from `readlinkat`; do not claim
that this removes the same-UID race.

Missing is only `ENOENT` from `os.stat(..., follow_symlinks=False)`. Other
errors fail closed.

## Relative atomic writes and removals

`tempfile.mkstemp()` has no `dir_fd`; create an unpredictable private leaf in
the opened parent:

```python
tmp = f".tianshu-apply-{secrets.token_hex(16)}"
tmp_fd = os.open(
    tmp,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
    0o600,
    dir_fd=parent_fd,
)
write_all(tmp_fd, content)
os.fchmod(tmp_fd, mode)
os.fsync(tmp_fd)
record = os.fstat(tmp_fd)
os.replace(tmp, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
post = os.fstat(tmp_fd)  # fd stays bound across rename
require os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False).dev_ino == post.dev_ino
```

Use the equivalent unpredictable temporary name plus `os.symlink(...,
dir_fd=parent_fd)` and relative `os.replace()` for links. Clean temporary names
with relative `os.unlink()` only. Removals use only relative `os.unlink()` or
`os.rmdir()` according to the exact fd-rooted capture.

## Mutation journal and rollback postimage CAS

For every path, append a `MutationRecord(state="pending")` **before** the first
destructive syscall. Then:

- delete: intended postimage is `missing`; after `unlink/rmdir` returns, capture
  and require `missing`, then set `owned_postimage` and `state="applied"`;
- regular/link replace: build the temp object first, set pending, atomically
  replace, capture the fd-rooted postimage, require it matches the intended
  bytes/mode and temp inode, then set applied;
- a failure between syscall and state update is resolved by capture: exact
  preimage means no mutation; exact intended/temp identity (or missing for
  delete) means owned mutation; anything else is indeterminate and rollback
  must refuse.

Rollback visits records in reverse order. Before any destructive rollback:

1. capture current through the same pinned root/parent fd;
2. if current is the exact original preimage, perform no action;
3. if current is not the exact `owned_postimage`, raise
   `WorkspaceMaterializationError` and preserve it;
4. only then atomically restore the backup via a relative private temp, or
   remove the exact owned postimage when the preimage was missing;
5. verify the restored **logical** preimage (kind, mode, bytes/link target).

Exact owned equality includes kind, `dev`, `ino`, mode, size, mtime, ctime, and
content digest. The digest catches same-inode/same-size writes; inode catches an
atomic replacement with identical bytes. Restored files get a new inode, so
final restoration verification is logical rather than original-inode equality.

Created parents are removed last, deepest first, only when their current
`dev/ino/mode` equals `OwnedDirectory.identity`; `rmdir` must also prove empty.
If any postimage or created-parent CAS fails, preserve external state, report
`rollback_failed`, keep the lease `cleanup_failed`, and persist no false
"restored" evidence.

## Deterministic tests / hook points

Keep one optional internal `race_hook(stage, RelativePath)` in
`RootedDirectory`; it is a scheduling seam, not a policy authority.

Required RED tests before implementation:

1. `after_leaf_stat_before_open`: replace a regular leaf with a symlink to an
   outside secret. `read_worktree_entry` fails closed and never returns outside
   bytes.
2. `after_leaf_open_before_read`: rename/replace the published leaf. The read
   returns the already-open regular object's bytes, never the replacement.
3. `after_parent_open_before_leaf`: replace a parent name with an outside
   symlink. Materialization never changes the outside victim; final published
   parent/root identity check reports drift.
4. `after_materialize_before_failure`: modify an applied regular file in place
   with the same inode and size, then inject failure. Rollback CAS refuses,
   external bytes remain, receipt is `rollback_failed`.
5. Same as (4), but atomically replace the leaf inode; external file remains.
6. Apply a previously missing path, externally delete/recreate it, then fail;
   rollback does not unlink the external recreation.
7. Change an applied symlink target, then fail; rollback preserves the external
   link.
8. Replace an owned newly-created parent before rollback; outside content is
   untouched and rollback fails closed.
9. Happy rollback: unchanged exact owned postimages restore every original
   byte/mode/link and remove only exact owned directories.
10. Swap the published source root after `RootedDirectory.open`; all mutations
    remain on the pinned original fd, the substituted tree is untouched, and
    `assert_published_root()` rejects final success.

Do not add a test that claims safety for `after_rollback_cas_before_unlink` or
`after_rollback_cas_before_replace`; that is the documented residual below.

## macOS / Python 3.12 evidence and residual risk

Locally verified on macOS with CPython 3.12.12:

- `O_DIRECTORY`, `O_NOFOLLOW`, `O_CLOEXEC`, and macOS `O_SYMLINK` are present;
- `os.open(..., dir_fd=...)`, `os.stat(..., dir_fd=...,
  follow_symlinks=False)`, `os.readlink`, `os.mkdir`, `os.unlink`, `os.rmdir`,
  `os.symlink`, `os.rename`, and `os.replace(src_dir_fd=..., dst_dir_fd=...)`
  work relative to an opened directory;
- `O_PATH` is absent on macOS, so do not design around it.

POSIX/macOS provides no stdlib atomic "compare this inode, then unlink/replace
this name" operation. `flock` is advisory, and macOS lacks Linux `renameat2`
CAS semantics. Therefore an uncooperative process running as the same UID can
still swap the final leaf name in the tiny interval between the last postimage
CAS and `unlink/replace`. Root-anchored dirfds eliminate parent/symlink redirect
and fd reads eliminate `lstat/read_bytes` confusion, while the postimage journal
detects all changes observed before rollback. The remaining same-UID leaf
check/use race must be stated as a trust-boundary limitation; it cannot be
honestly marked eliminated without OS isolation (different UID/container) or a
platform-specific kernel primitive.

## Implementation order

1. Add `rooted_fs.py` and its low-level hook tests.
2. Move `GitBackend.read_worktree_entry()` to fd capture; keep its public return
   type unchanged.
3. Introduce one `ApplyFsSession`; migrate backup/materialize/verify/restore and
   remove the parallel pathname implementation.
4. Add the mutation journal/postimage rollback tests.
5. Close the session in an unconditional service `finally`; map postimage drift
   to `rollback_failed`, not `source_drift` or a successful rollback receipt.
