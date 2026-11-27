# Test signing key

`dede-update-test.key` signs test releases and CI update tests. It is
public on purpose: only images built with
`build/config/includes.chroot/usr/share/dede-tv/update-keys/dede-test.pem`
trust it. Production images get their own public key and must not ship
`dede-test.pem`; the production private key stays in a GitHub secret.
