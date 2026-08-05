import pkg from "../package.json";

/**
 * The web client's build-time version, read straight from
 * `package.json` (bundled at build time, not evaluated at runtime).
 *
 * `package.json`'s `version` field is kept in MANUAL sync with the
 * server's `app.__version__` (`app/__init__.py`) at each release -- there
 * is no automated link between the two. If they ever drift, `VersionBanner`
 * catches it live via the `/meta` handshake (server_version vs
 * `CLIENT_VERSION`) rather than failing silently, so a missed manual bump
 * degrades to a visible banner instead of an invisible mismatch.
 */
export const CLIENT_VERSION: string = pkg.version;
