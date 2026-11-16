"""Structured audit logging for the auth lifecycle.

A dedicated logger, ``auth.audit``, is wired in ``config/settings.py`` to both
the console and ``SERVER_LOG_FILE``. Every record is emitted as a single
message with key=value fields so it is both greppable and scriptable.

Security rules enforced here:
* never log a password, its hash, or a session/token key;
* include only the fields needed to reconstruct *what* happened: username,
  client IP, endpoint, outcome, and a reason *class* (not the offending value);
* the reason for a failed password change is the validation class name, never
  the password that was rejected.
"""

import logging

audit = logging.getLogger('auth.audit')


def _fields(request, **extra):
    """Merge request-derived audit fields with any caller-supplied ones.

    ``extra`` wins over the derived values; only truthy values are emitted so a
    missing IP does not produce a sentinel token.
    """
    fields = {
        'ip': _client_ip(request),
        'path': getattr(request, 'path', None),
        **extra,
    }
    return {k: v for k, v in fields.items() if v is not None}


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or None


def _emit(level, event, request, **extra):
    """Log ``event`` with structured ``extra`` fields at the given level."""
    fields = _fields(request, **extra)
    line = ' '.join(f'{k}={v}' for k, v in fields.items())
    audit.log(level, 'auth_event=%s %s' % (event, line))


def login_success(request, username, forced_change=False):
    _emit(logging.INFO, 'login_success', request,
          username=username, outcome='success',
          forced_change=forced_change or None)


def login_failure(request, username, blocked=False):
    _emit(logging.WARNING, 'login_failure', request,
          username=username, outcome='failure',
          blocked=blocked or None)


def forced_change_required(request, username):
    _emit(logging.WARNING, 'forced_change_required', request,
          username=username, outcome='forced_change')


def password_change_success(request, username):
    _emit(logging.INFO, 'password_change_success', request,
          username=username, outcome='success')


def password_change_failure(request, username, reason_class, blocked=False):
    _emit(logging.WARNING, 'password_change_failure', request,
          username=username, outcome='failure',
          reason_class=reason_class,
          blocked=blocked or None)


def guard_blocked(request, username):
    _emit(logging.WARNING, 'guard_blocked', request,
          username=username, outcome='blocked_403')
