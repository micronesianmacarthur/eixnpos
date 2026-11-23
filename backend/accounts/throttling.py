"""Failed-attempt rate limiting for the auth endpoints.

Brute-force protection that counts *failed* attempts per identity (username)
and client IP, and returns HTTP 429 once the limit for the window is reached.
A successful attempt clears the counter, so a legitimate user is never locked
out as long as they can authenticate; the window also expires on its own, so a
lock is never indefinite.

Two independent namespaces are used:

* ``login`` — a wrong password against ``POST /api/auth/login/``.
* ``change_password`` — a wrong current password at the start of
  ``POST /api/auth/change-password/`` (new-password *policy* violations are not
  security events and deliberately do not count toward the lockout).
* ``admin_login`` — a rejected credential at ``/admin/login/`` (the hardened
  Django admin site; see ``accounts/admin_site.py``).

Limits and windows are defined in ``config/settings.py``.
"""

from django.conf import settings


def client_ip(request):
    """Return the end-client IP that keys throttling and audit records.

    ``X-Forwarded-For`` is trusted only when the immediate peer (``REMOTE_ADDR``)
    is one of the deployment's configured trusted proxies
    (``settings.AUTH_TRUSTED_PROXY_IPS``, empty by default). Otherwise the
    header is ignored and ``REMOTE_ADDR`` is used, so an untrusted client
    cannot rotate its own throttle-key identity or spoof its audit IP. See the
    deployment note next to ``AUTH_TRUSTED_PROXY_IPS`` in ``config/settings.py``.
    """
    remote = (request.META.get('REMOTE_ADDR') or '').strip()
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').strip()
    trusted = set(getattr(settings, 'AUTH_TRUSTED_PROXY_IPS', None) or ())
    if forwarded and remote in trusted:
        return forwarded.split(',')[0].strip()
    return remote


def _limit_and_window(namespace):
    if namespace == 'login':
        return settings.AUTH_LOGIN_FAIL_LIMIT, settings.AUTH_LOGIN_FAIL_WINDOW
    if namespace == 'change_password':
        return (settings.AUTH_CHANGE_PASSWORD_FAIL_LIMIT,
                settings.AUTH_CHANGE_PASSWORD_FAIL_WINDOW)
    if namespace == 'admin_login':
        return (settings.AUTH_ADMIN_LOGIN_FAIL_LIMIT,
                settings.AUTH_ADMIN_LOGIN_FAIL_WINDOW)
    raise ValueError(f'unknown throttle namespace: {namespace}')


def _key(namespace, identity, ip):
    return f'auth:fail:{namespace}:{identity}:{ip}'


def is_blocked(namespace, identity, ip):
    """True when ``identity``/``ip`` has exhausted the failure budget."""
    from django.core.cache import cache
    limit, _ = _limit_and_window(namespace)
    return cache.get(_key(namespace, identity, ip), 0) >= limit


def register_failure(namespace, identity, ip):
    """Record one failed attempt; expires after the configured window."""
    from django.core.cache import cache
    _, window = _limit_and_window(namespace)
    key = _key(namespace, identity, ip)
    cache.set(key, cache.get(key, 0) + 1, timeout=window)


def reset(namespace, identity, ip):
    """Clear the failure counter (called after a successful attempt)."""
    from django.core.cache import cache
    cache.delete(_key(namespace, identity, ip))
