class _SerializationDisabled:
    __slots__ = ()

    def __reduce__(self) -> object:
        raise TypeError("serialization is disabled")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("serialization is disabled")


class _SensitiveValue(_SerializationDisabled):
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("invalid sensitive value")
        object.__setattr__(self, "_SensitiveValue__value", value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("sensitive values are immutable")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"

    __str__ = __repr__

    def _reveal(self) -> str:
        return self.__value


class _RemoteCredential(_SensitiveValue):
    __slots__ = ()

    def reveal_for_remote_call(self) -> str:
        return self._reveal()


class IdToken(_RemoteCredential):
    __slots__ = ()


class UserAccessToken(_RemoteCredential):
    __slots__ = ()


class ChannelAccessToken(_RemoteCredential):
    __slots__ = ()


class LineSubject(_SensitiveValue):
    __slots__ = ()

    def reveal_for_identity_binding(self) -> str:
        return self._reveal()
