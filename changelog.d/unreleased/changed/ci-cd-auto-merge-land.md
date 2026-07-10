- Development work now lands on `main` through auto-merging pull requests
  instead of direct pushes: `scripts/land` pushes the branch, opens a
  non-draft PR, and enables auto-merge, and GitHub merges it once the CI
  checks — now required by branch protection on `main` — pass.
