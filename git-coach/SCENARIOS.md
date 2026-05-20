# git-coach scenarios

Real-world git situations as **setup → trap → fix → why**. Where `painpoints.toml` is a single-command dictionary, these capture the *sequences* — the multi-step recoveries where the naïve mental model breaks. Each links to the relevant pain-point ids.

---

## Renaming a mislabeled, already-pushed branch + commit

You committed and pushed work under the wrong ticket number — you grabbed the **Epic** (`XSYS-1574`) instead of the work **Item** (`XSYS-1592`). Both the commit message *and* the branch name carry the wrong number, the branch is already on `origin`, and you have unrelated work-in-progress in the tree (some of it staged).

### The traps

- **`git commit --amend` is not "just edit the message."** It re-commits the **index**. Any *staged* changes get silently folded into the reworded commit. With unrelated WIP staged, you'd bake it into the wrong commit.
- **A remote has no "rename."** `git branch -m` only renames *locally*, and the renamed branch keeps tracking the *old* upstream. Renaming on the server is really *push-new + delete-old*.
- **The `--amend` summary looks scary.** It prints e.g. `10 files changed, 261 insertions(+)` — that's the commit's diff **against its parent** (the whole commit), not what the amend just added.
- **Deleting the old remote branch can close an open MR/PR** silently.

### The fix

```bash
# 0. safety net — keep the original commit reachable no matter what
git branch backup/xsys-1574-orig <sha>

# 1. clear the index so the amend is message-only
git reset

# 2. reword the tip commit (content unchanged)
git commit --amend -m "XSYS-1592: VPR single activity implemented"

# 3. rename locally
git branch -m task/XSYS-1592-PTT-Support-VPR-with-single-Activity

# 4. publish the new name (repoints upstream), then delete the old remote branch
git push -u origin task/XSYS-1592-PTT-Support-VPR-with-single-Activity
git push origin --delete task/XSYS-1574-PTT-Support-VPR-with-single-Activity
```

### Why it works

- **`git reset`** empties the staging area *without touching files*, so `--amend` reproduces the original tree with only a new message — your WIP stays uncommitted.
- **Renaming a pushed branch = push-new + delete-old.** The `-u` on step 4 repoints the upstream from the stale `…1574` to `…1592` (the rename in step 3 alone left it tracking the old one).
- **No `--force` needed here** — the corrected commit ships under a *new* branch name, so there's nothing to overwrite. Had you kept the *same* branch name, the new commit hash would require `git push --force-with-lease`.
- **The backup ref makes the whole thing undoable** — `git reset --hard backup/xsys-1574-orig` restores the exact pre-fix state. Delete it when satisfied: `git branch -D backup/xsys-1574-orig`.

### Remember

- Reference the work **Item**, not the parent **Epic**, in commit/branch names.
- If an MR was open on the old branch, reopen it on the new one — or rename via the GitHub/GitLab **web UI**, which preserves the MR instead of closing it.

**Related pain-points:** `safety.backup-before-rewrite` · `undo.unstage-all` · `commit.amend-message` · `branch.rename-local` · `branch.rename-pushed` · `branch.delete-remote` · `push.force-with-lease`
