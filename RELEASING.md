# Releasing hacs-e3dc

HACS offers each GitHub release as an update. Users only see what is released, never a push to `main`.

## Versions

A release tag is `v` plus the manifest version, for example `v0.1.0b1` for manifest version `0.1.0b1`. Use beta versions (`0.1.0b1`, `0.1.0b2`, ...) and mark those releases as pre-releases while the integration is being tested, so only users who opt into beta versions in HACS get them.

## Releasing

1. Check that the source commits are the ones to release, and that their CI is green: `e3dc-ess-modbus` and `ha-core-e3dc` on `main`.
2. Vendor them with the new version:

   ```bash
   scripts/vendor.py --version 0.1.0b2
   ```

   The script refuses checkouts with uncommitted changes, and writes the commits it vendored to `VENDORED`.
3. Run `python3 -m pytest` and `scripts/lint`, commit, push to `main`, and wait for CI to pass.
4. On GitHub, draft a release with the tag `v0.1.0b2`, created on `main`, and the version as title. Name the vendored commits from `VENDORED` in the notes. Tick **Set as a pre-release** for a beta.
5. Publish the release.

## Staying compatible with the core integration

Until the `e3dc` integration is part of Home Assistant, users are meant to move from this custom integration to the core one without setting anything up again. Keep these identical to `ha-core-e3dc`, which vendoring does as long as nothing is changed here by hand:

- the config entry `VERSION` and `MINOR_VERSION`,
- the config entry unique ID (the serial number),
- every entity's unique ID and every device identifier.
