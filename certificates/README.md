# Dataset HTTPS intermediate

`sectigo-dv-r36.pem` is the public Sectigo Public Server Authentication CA DV R36
intermediate, obtained from the issuer URL in the dataset server's certificate:
http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt

On 22 October 2026, datasets.simula.no served a new leaf certificate with an
unrelated older intermediate chain. Python/OpenSSL rejected the download with
`unable to get local issuer certificate`.

The downloader supplements the system trust store with this intermediate and
disables partial-chain trust. Hostname checks, certificate validity and a chain
to a system-trusted root remain required. No root certificate is bundled and
TLS verification is never disabled. The intermediate was verified with
`openssl verify` against the system roots before inclusion.

If the host changes certificate issuers, update this intermediate from the CA
and verify its chain again. Corporate HTTPS interception may also require the
organization's CA through `SSL_CERT_FILE`.
