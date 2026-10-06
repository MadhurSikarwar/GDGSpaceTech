"""Space-Track login: network and TLS hiccups are retried, a refused login is not."""
import pytest
import requests

from orbitwatch.jobs import spacetrack


class _Resp:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text


def _client(monkeypatch, outcomes):
    monkeypatch.setattr(spacetrack, "credentials", lambda: ("user@example.test", "not-a-real-password"))
    monkeypatch.setattr(spacetrack.time, "sleep", lambda s: sleeps.append(s))
    st = spacetrack.SpaceTrack()
    calls = []

    def post(url, **kw):
        calls.append(url)
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out
    monkeypatch.setattr(st.session, "post", post)
    return st, calls


sleeps = []


def test_st01_tls_hiccup_is_retried_with_backoff(monkeypatch):
    sleeps.clear()
    st, calls = _client(monkeypatch, [requests.exceptions.SSLError("incomplete chain"),
                                      requests.exceptions.ConnectionError("reset"), _Resp(200, "")])
    st.login()
    assert st.logged_in and len(calls) == 3 and sleeps == [3, 10]


def test_st02_gives_up_after_the_last_attempt_and_still_raises(monkeypatch):
    sleeps.clear()
    st, calls = _client(monkeypatch, [requests.exceptions.SSLError("x")] * 4)
    with pytest.raises(requests.exceptions.SSLError):
        st.login()
    assert len(calls) == 4 and not st.logged_in


def test_st03_a_refused_login_is_not_retried(monkeypatch):
    sleeps.clear()
    st, calls = _client(monkeypatch, [_Resp(200, '{"Login":"Failed"}'), _Resp(200, "")])
    with pytest.raises(RuntimeError, match="login failed"):
        st.login()
    assert len(calls) == 1 and sleeps == []
