"""Thin wrapper around `tableauserverclient` (TSC) for exactly what Workbook Compare
needs: signing in, listing workbooks/custom views live (no local cache), resolving a
workbook by id or name, downloading a workbook's published definition (no extract),
and rendering a custom view's saved-state preview image.

Trimmed from tableau-admin-dashboard/tableau_client.py - that version also covers
projects/datasources/users/permissions/jobs/etc. for the full admin dashboard; none of
that is reachable from this app.
"""
import contextlib
import json
import time
import urllib.request

import tableauserverclient as TSC

VERSION_PROBE_ATTEMPTS = 3
VERSION_PROBE_RETRY_DELAY_SECONDS = 1.5


class ServerUnreachableError(Exception):
    """Raised when TSC can't determine the server's REST API version - usually a
    transient proxy/VPN/load-balancer hiccup on the unauthenticated /api/serverinfo
    probe TSC makes before sign-in. Without this check, that failure surfaces later as
    a cryptic `packaging.version.InvalidVersion: Invalid version: 'Unknown'` with no
    context. We retry a few times first since this has been observed to be
    intermittent (e.g. an F5 BIG-IP pool occasionally routing to a bad node)."""


def _connect_with_known_version(server_url: str) -> TSC.Server:
    last_seen_version = None
    for attempt in range(1, VERSION_PROBE_ATTEMPTS + 1):
        server = TSC.Server(server_url, use_server_version=True)
        if server.version and server.version != "Unknown":
            return server
        last_seen_version = server.version
        if attempt < VERSION_PROBE_ATTEMPTS:
            time.sleep(VERSION_PROBE_RETRY_DELAY_SECONDS)

    raise ServerUnreachableError(
        f"Could not determine the Tableau REST API version from {server_url}/api/2.4/serverinfo "
        f"after {VERSION_PROBE_ATTEMPTS} attempts (got {last_seen_version!r} each time). This usually "
        "means a proxy, VPN, or load balancer is intermittently returning something other than the "
        "expected XML rather than a bad token - try again shortly, or run "
        f"'curl {server_url}/api/2.4/serverinfo' repeatedly from the same machine to see how often it "
        "happens outside this app too."
    )


@contextlib.contextmanager
def signed_in_server(server_url: str, site_name: str, pat_name: str, pat_secret: str):
    """Yields a signed-in TSC Server object.

    Signs in and out explicitly (rather than via TSC's own `with
    server.auth.sign_in(auth):` sugar) so a failed *sign-out* can't masquerade as a
    failed operation. `server.auth.sign_in()` performs the real sign-in immediately
    and only returns a thin wrapper whose __exit__ calls sign_out() - so using it as
    a context manager means any exception from THAT sign_out() call (e.g. another
    sign-in with the same PAT already invalidated this session - exactly the
    scenario call_with_retry exists for) propagates out of the `with` block,
    turning a caller's already-successful result into an apparent NotSignedInError
    and forcing a pointless (and, if it happens twice in a row, fatal) retry of
    work that already succeeded. A failed sign-out has nothing meaningful left to
    clean up - the session is already dead one way or another - so it's swallowed."""
    server = _connect_with_known_version(server_url)
    auth = TSC.PersonalAccessTokenAuth(pat_name, pat_secret, site_id=site_name)
    server.auth.sign_in(auth)
    try:
        yield server
    finally:
        try:
            server.auth.sign_out()
        except Exception:
            pass


def call_with_retry(server_url: str, site_name: str, pat_name: str, pat_secret: str, fn):
    """Signs in, runs `fn(server)`, and retries ONCE with a brand-new sign-in if the
    session was invalidated mid-call (TSC.NotSignedInError - raised for any 401,
    including a session that simply expired; see the "TODO: catch this in server.py
    and attempt to sign in again" comment in TSC's own endpoint.py, which never got
    implemented, leaving retry-on-session-expiry to callers like this one).

    This PAT is also used by other apps/schedulers signing in independently against
    the same Tableau Server account - any of those sign-ins can invalidate this
    session's token mid-request, not just a slow request outliving its own timeout.
    A fresh sign-in is cheap, so retrying once here is enough for both causes."""
    for attempt in range(2):
        try:
            with signed_in_server(server_url, site_name, pat_name, pat_secret) as server:
                return fn(server)
        except TSC.NotSignedInError:
            if attempt == 1:
                raise


def list_workbooks(server) -> list:
    """Returns WorkbookItem objects for every workbook on the site."""
    return list(TSC.Pager(server.workbooks))


def find_workbook_by_id_or_name(server, id_or_name: str):
    """Resolves a workbook for Workbook Compare - tries as a LUID first, then falls
    back to a unique name match (case-insensitive) since the compare UI lets a user
    pick by name from the live workbook list."""
    try:
        return server.workbooks.get_by_id(id_or_name)
    except Exception:
        pass

    matches = [wb for wb in TSC.Pager(server.workbooks) if wb.name.lower() == id_or_name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"Workbook name '{id_or_name}' is not unique on this site - {len(matches)} matches.")
    return None


def download_workbook_definition(server, workbook_id: str, download_dir: str) -> str:
    """Downloads the PUBLISHED workbook's definition (.twbx) WITHOUT the extract, for
    Workbook Compare. Read-only - never publishes, overwrites, or mutates anything on
    the server. Returns the local file path."""
    return server.workbooks.download(workbook_id, filepath=download_dir, include_extract=False)


def list_custom_views(server) -> list:
    """Returns (id, name, workbook_name, view_name, owner_name, shared, created_at,
    updated_at, last_accessed_at) tuples for every Custom View (Tableau's
    saved-view-per-user feature) the signed-in account can see. Non-admin PATs may
    only see custom views they own or that are marked shared (a Tableau Server-side
    restriction, not something this app controls).

    Uses a raw REST call with `includeUsageStatistics=true` instead of TSC's
    `server.custom_views.get()`/`TSC.Pager`, because TSC (as of 0.32) doesn't expose
    that query parameter for the custom-views endpoint even though the underlying
    Tableau REST API (3.21+) supports it and returns `lastAccessedAt` per view. Each
    item is independently try/except-wrapped so one malformed entry doesn't drop the
    rest."""
    rows = []
    page_number = 1
    page_size = 100
    while True:
        url = (
            f"{server.custom_views.baseurl}"
            f"?includeUsageStatistics=true&pageSize={page_size}&pageNumber={page_number}"
        )
        request = urllib.request.Request(
            url,
            headers={"X-Tableau-Auth": server.auth_token, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        items = payload.get("customViews", {}).get("customView") or []
        if isinstance(items, dict):
            # Tableau's JSON API collapses a single-item page to a dict instead of a list.
            items = [items]

        for cv in items:
            try:
                workbook = cv.get("workbook") or {}
                view = cv.get("view") or {}
                owner = cv.get("owner") or {}
                rows.append(
                    (
                        cv.get("id"),
                        cv.get("name"),
                        workbook.get("name"),
                        view.get("name"),
                        owner.get("name"),
                        1 if cv.get("shared") else 0,
                        cv.get("createdAt"),
                        cv.get("updatedAt"),
                        cv.get("lastAccessedAt"),
                    )
                )
            except Exception:
                continue

        pagination = payload.get("pagination", {})
        total_available = int(pagination.get("totalAvailable", 0))
        if page_number * page_size >= total_available:
            break
        page_number += 1

    return rows


def get_custom_view_image(server, view_id: str) -> bytes:
    """Downloads the rendered PNG of a custom view exactly as saved - filters,
    parameters, and sort order all baked into the image as Tableau last rendered
    them. This is the closest thing to inspecting a custom view's saved state that
    the Tableau REST API exposes: there is no endpoint that returns the underlying
    filter/parameter/sort *values* themselves (verified directly against this
    server - the "content"/full-definition endpoint used by Tableau's Migration SDK
    404s here, and the image/PDF/data endpoints only support their one native
    format each, no JSON)."""
    url = f"{server.custom_views.baseurl}/{view_id}/image"
    # No explicit Accept header - Tableau's image endpoint returns 406 Not
    # Acceptable for "Accept: image/png" specifically (confirmed against the real
    # server); it's happy with the default (no Accept header at all).
    request = urllib.request.Request(url, headers={"X-Tableau-Auth": server.auth_token})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()
