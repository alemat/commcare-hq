import os
from base64 import urlsafe_b64encode


class ClosingContextProxy(object):
    """Context manager wrapper for object with close() method

    Calls `wrapped_object.close()` on exit context.
    """

    def __init__(self, obj):
        self._obj = obj

    def __getattr__(self, name):
        return getattr(self._obj, name)

    def __iter__(self):
        return iter(self._obj)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._obj.close()


class document_method(object):
    """Document method

    A document method is a twist between a static method and an instance
    method. It can be called as a normal instance method, in which case
    the first argument (`self`) is an instance of the method's class
    type, or it can be called like a static method:

        Document.method(obj, other, args)

    in which case the first argument is passed as `self` and need not
    be an instance of `Document`.
    """

    def __init__(self, func):
        self.func = func

    def __get__(self, obj, owner):
        if obj is None:
            return self.func
        return self.func.__get__(obj, owner)


def random_url_id(nbytes):
    """Get a random URL-safe ID string

    :param nbytes: Number of random bytes to include in the ID.
    :returns: A URL-safe string.
    """
    return urlsafe_b64encode(os.urandom(nbytes)).decode('ascii').rstrip(u'=')
