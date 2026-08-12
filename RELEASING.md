# Releasing Perf Sentinel for JetBrains IDEs

This runbook publishes one stable `0.x.y` plugin ZIP. GitHub builds it twice on
Windows and compares the unsigned bytes. A protected job then signs that exact
ZIP once and sends the same author-signed bytes to JetBrains Marketplace and
GitHub. Marketplace adds a second JetBrains signature before distribution.

## One-time activation

1. Make the source repository public and create the JetBrains Marketplace
   vendor profile and plugin page. Accept the Marketplace Developer Agreement,
   use plugin ID `io.github.robintra.perfsentinel`, select the AGPL-3.0-only
   license, and link back to this public repository. JetBrains requires one
   initial manual upload before Gradle can publish updates. Upload the first
   reviewed author-signed stable ZIP manually and keep it hidden while its
   listing and signature are checked. Use the automated workflow for every
   later stable version.
2. Obtain the production plugin-signing certificate and private key. Replace
   the `pending_activation` certificate in `config/signing-identities.json`
   with the exact public common name and SHA-256 fingerprint. Review that
   change before adding any secret.
3. Create the protected GitHub environment `jetbrains-release`. Require a
   maintainer approval and allow deployments only from stable version tags.
4. Add these environment secrets:

   - `CERTIFICATE_CHAIN`: public PEM certificate chain;
   - `PRIVATE_KEY`: matching private PEM key;
   - `PRIVATE_KEY_PASSWORD`: key password;
   - `PUBLISH_TOKEN`: least-privilege JetBrains Marketplace token.

The private key and token must never be committed or copied into artifacts.
GitHub Actions keeps the normal failed-workflow notifications; there is no
release-alert issue bot.

## Publish a stable version

1. Update `gradle.properties`, `plugin.xml`, and `CHANGELOG.md` to the same
   stable `0.MINOR.PATCH` version, merge the reviewed change to `main`, and
   make sure local `main` exactly matches `origin/main`.
2. Check the release without changing Git or any service:

   ```shell
   scripts/release.sh v0.1.0 --dry-run
   ```

3. Run `scripts/release.sh v0.1.0`, then enter that exact tag when prompted.
   The script creates and pushes one verified SSH-signed tag.
4. The release workflow verifies the tag against
   `config/release-allowed-signers`, builds twice on `windows-2025`, compares
   the archives byte for byte, and pauses at the `jetbrains-release`
   environment approval.
5. Approve only after checking the tag, version, and compared-artifact digest.
   The protected job signs once, verifies the native JetBrains signature,
   creates an SPDX 2.3 SBOM and a closed release manifest, uploads the stable
   Marketplace version, and creates the GitHub release with exactly four
   public assets.

Every Marketplace upload is reviewed by JetBrains before it becomes publicly
available. The daily secret-free workflow will remain red until the exact
version can be downloaded from both GitHub and Marketplace; that is expected
during review, not a reason to republish or move the tag.

## Public verification

The scheduled `Release Verification` workflow downloads the latest stable
GitHub release and this exact Marketplace URL:

```text
https://plugins.jetbrains.com/plugin/download?pluginId=io.github.robintra.perfsentinel&version=VERSION
```

It requires exactly the signed ZIP, release manifest, SPDX document, and public
certificate chain on GitHub. It then checks the certificate identity, native
author signature, manifest hashes, and exact plugin entries in both ZIPs. The
two full archives are not expected to be byte-identical because Marketplace
adds its own valid signature. The workflow uses no repository secret.

## Rotation

For a Marketplace token rotation, revoke the old token first, replace only
`PUBLISH_TOKEN` in `jetbrains-release`, and let the next stable release exercise
it. Do not test by republishing an existing version.

For a certificate rotation, generate the key outside the repository, update
the four protected environment secrets together, and commit the new public
common name and fingerprint in `config/signing-identities.json` through review.
Keep the certificate-chain asset on each historical GitHub release. Verify a
fresh candidate before approving its protected release job.

For a Git tag signer rotation, update `config/release-allowed-signers` in a
reviewed commit before using the new key. Keep old public keys while an existing
release tag still needs to be auditable.

## Rollback and incident response

Stop the `jetbrains-release` environment first and revoke the affected key or
token. Hide the Marketplace version from the Versions page and remove or draft
the GitHub release. Keep the evidence needed to investigate. Create a corrected
patch version after the fix; **do not move or reuse the tag** and do not
overwrite an existing Marketplace version.

If a version is still under Marketplace review, hide or withdraw it in the
Marketplace portal. If an approved version must be removed, hide that version
and contact JetBrains Marketplace support when necessary. The next release must
use a new stable version and pass the complete workflow again.

## Deliberate scope

The JetBrains native signature is the plugin authenticity mechanism. There is
**No Cosign** signature for the ZIP and **No SLSA** level claim. Those would add
another format without improving the IDE's native verification path. The SPDX
SBOM and exact public plugin-entry comparison remain part of every release.
