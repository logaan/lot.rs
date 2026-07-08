- Releases now update the Homebrew tap automatically: the new
  `scripts/update-tap` regenerates the tap's formula from a release's
  published checksums, and `scripts/release` runs it once the release assets
  are up.
