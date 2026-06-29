from contextvars import ContextVar


_current_tenant_alias = ContextVar('current_tenant_alias', default=None)


def set_current_tenant_alias(alias):
    return _current_tenant_alias.set(alias)


def get_current_tenant_alias():
    return _current_tenant_alias.get()


def reset_current_tenant_alias(token):
    if token is not None:
        _current_tenant_alias.reset(token)
