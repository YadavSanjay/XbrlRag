"""C8: an error text is tagged transport or the arm's own (requirement 13)."""

from __future__ import annotations

import psycopg2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import error_origin, error_text
from src.llm.gateway.adapters.base import TRANSIENT_STATUS, classify_transient

TRANSPORT = "transport"  # requirement 13: the two tags, as literals
ARM = "arm"

# oracle: requirement 13, case by case
TABLE = [
    ("RateLimitError('Error code: 429 - {\"error\": {\"message\": \"Resource Unavailable\"}}')",
     TRANSPORT),
    ("InternalServerError('Error code: 500 - upstream')", TRANSPORT),
    ("APIStatusError('Error code: 503 - unavailable')", TRANSPORT),
    ("ConflictError(\"Error code: 409 - {'error': 'busy'}\")", TRANSPORT),
    ("BadRequestError('Error code: 400 - prompt too long')", ARM),
    ("APIError('Error code: 400 - bad request')", ARM),  # the status decides
    ("AuthenticationError('Error code: 401 - bad key')", ARM),
    ("APIConnectionError('Connection error.')", TRANSPORT),
    ("APITimeoutError('Request timed out.')", TRANSPORT),
    ("ReadTimeout('timed out')", TRANSPORT),
    ("OverloadedError('overloaded')", TRANSPORT),
    ("TimeoutError()", TRANSPORT),
    ("ConnectionResetError(10054, 'An existing connection was forcibly closed')", TRANSPORT),
    ("BrokenPipeError(32, 'Broken pipe')", TRANSPORT),
    ("CircuitBreakerOpen('3 consecutive LLM failures >= limit 3')", TRANSPORT),
    ("RunBudgetExceeded('run cost $5.0000 >= limit $5.0000')", TRANSPORT),
    ("ValueError('reply has no JSON object')", ARM),
    ("KeyError('steps')", ARM),
    ("LLMEmptyReplyError('empty reply')", ARM),
    ("excluded: the filing is not in the corpus", ARM),
    ("pipeline construction failed: OperationalError('could not connect')", ARM),
    ("ReadTimeout", ARM),  # no "Name(" form: doubtful, so the arm's own
]


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("text,expected", TABLE)
def test_error_origin_table(text, expected):
    # oracle: requirement 13 — the table above
    assert error_origin(text) == expected


@pytest.mark.criterion("C8")
def test_no_error_has_no_origin():
    # oracle: requirement 13 — None or empty text is no error; a real error is tagged
    assert error_origin("ValueError('x')") == ARM
    assert error_origin(None) is None
    assert error_origin("") is None


_NAMES = ["RateLimitError", "APIError", "InternalServerError", "APIConnectionError",
          "OverloadedError", "ReadTimeout", "ConnectionError", "BadRequestError", "ValueError",
          "AuthenticationError", "NotFoundError", "KeyError", "PermissionDeniedError"]


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(name=st.sampled_from(_NAMES), status=st.one_of(st.none(), st.integers(400, 599)),
       quote=st.booleans())
def test_agrees_with_the_gateway_classifier(name, status, quote):
    # oracle: classify_transient, the gateway's own retry rule (requirement 13 mirrors it)
    cls = type(name, (Exception,), {})
    if status is None:
        exc = cls("it's broken" if quote else "broken")
    else:
        exc = cls(f"Error code: {status} - {{'error': {{'type': 'x'}}}}" if quote
                  else f"Error code: {status} - body")
        exc.status_code = status
    want = TRANSPORT if classify_transient(exc) else ARM
    assert error_origin(repr(exc)) == want


# ---------------------------------------------------------------------------- our services
# Stand-ins with the client libraries' class names and message formats, as httpx,
# requests, qdrant_client and psycopg2 produced them in the py312 env on 2026-11-04 (see
# the spec's Notes). Spec tests may not import the HTTP clients; error_origin reads only the
# class name and the message, so a stand-in gives the same text. Nothing is sent anywhere.

_RAGINFER = "http://127.0.0.1:8799"


class HTTPStatusError(Exception):
    """httpx's raise_for_status error (RagInference's embedder and reranker clients)."""


class ConnectError(Exception):
    """httpx: the host refused or was unreachable."""


class RemoteProtocolError(Exception):
    """httpx: the server dropped the connection mid-response."""


class HTTPError(Exception):
    """requests' raise_for_status error."""


class ResponseHandlingException(Exception):
    """qdrant_client's wrapper for a failed request (connection, timeout)."""


class UnexpectedResponse(Exception):
    """qdrant_client's HTTP-status error. Like the real one, it calls Exception.__init__ with
    no arguments, so repr() is ``UnexpectedResponse()`` and only str() has the status."""

    def __init__(self, status_code: int, reason_phrase: str, content: bytes) -> None:
        super().__init__()
        self.status_code, self.reason_phrase, self.content = status_code, reason_phrase, content

    def __str__(self) -> str:
        return (f"Unexpected Response: {self.status_code} ({self.reason_phrase})\n"
                f"Raw response content:\n{self.content!r}")


def _httpx_status(code: int) -> BaseException:
    """httpx's message for a status: 'Server error' for 5xx, 'Client error' for 4xx."""
    kind = "Server error" if code >= 500 else "Client error"
    return HTTPStatusError(
        f"{kind} '{code} Reason' for url '{_RAGINFER}/rerank'\nFor more information check: "
        f"https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/{code}")


def _requests_status(code: int) -> BaseException:
    """requests' message for a status: 'NNN Server Error' or 'NNN Client Error'."""
    kind = "Server Error" if code >= 500 else "Client Error"
    return HTTPError(f"{code} {kind}: Reason for url: {_RAGINFER}/embed")


def _qdrant_status(code: int) -> BaseException:
    return UnexpectedResponse(code, "Reason", b"x")


SERVICES = [
    # RagInference (RAGINFER_URL: the remote embedder and reranker)
    ("raginfer httpx 503", lambda: _httpx_status(503), TRANSPORT),
    ("raginfer httpx 502", lambda: _httpx_status(502), TRANSPORT),
    ("raginfer httpx 429", lambda: _httpx_status(429), TRANSPORT),
    ("raginfer httpx 422", lambda: _httpx_status(422), ARM),
    ("raginfer requests 503", lambda: _requests_status(503), TRANSPORT),
    ("raginfer requests 400", lambda: _requests_status(400), ARM),
    ("raginfer down", lambda: ConnectError("[WinError 10061] connection refused"),
     TRANSPORT),
    ("raginfer reset", lambda: RemoteProtocolError("Server disconnected"), TRANSPORT),
    # Qdrant
    ("qdrant 503", lambda: _qdrant_status(503), TRANSPORT),
    ("qdrant 500", lambda: _qdrant_status(500), TRANSPORT),
    ("qdrant 404", lambda: _qdrant_status(404), ARM),
    ("qdrant down", lambda: ResponseHandlingException(
        ConnectError("All connection attempts failed")), TRANSPORT),
    # Postgres connection loss
    ("postgres closed", lambda: psycopg2.OperationalError(
        "server closed the connection unexpectedly\n\tThis probably means the server "
        "terminated abnormally"), TRANSPORT),
    ("postgres refused", lambda: psycopg2.OperationalError(
        'connection to server at "localhost" (::1), port 5432 failed: Connection refused'),
     TRANSPORT),
    ("postgres gone", lambda: psycopg2.InterfaceError("connection already closed"), TRANSPORT),
    # doubtful, so the arm's own
    ("postgres timeout", lambda: psycopg2.OperationalError(
        "canceling statement due to statement timeout"), ARM),
    ("postgres sql", lambda: psycopg2.ProgrammingError('column "x" does not exist'), ARM),
    ("reranker shape", lambda: RuntimeError("reranker returned 3 scores for 4 passages"), ARM),
]


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("label,build,expected", SERVICES, ids=[s[0] for s in SERVICES])
def test_our_services_errors(label, build, expected):
    # oracle: requirement 13 — 5xx (and 408/409/425/429) and connection loss from RagInference,
    # Qdrant and Postgres are transport, like a provider's; any other text is the arm's own
    assert error_origin(error_text(build())) == expected


@pytest.mark.criterion("C8")
def test_error_text_is_repr_unless_repr_drops_the_message():
    # oracle: requirement 13 — repr() when it carries the message; otherwise Name(str(exc))
    assert error_text(ValueError("bad plan")) == "ValueError('bad plan')"
    exc = _httpx_status(503)
    assert error_text(exc) == repr(exc)
    qdrant = _qdrant_status(503)
    assert repr(qdrant) == "UnexpectedResponse()"  # why the rule exists
    assert error_text(qdrant) == "UnexpectedResponse(" + repr(str(qdrant)) + ")"
    assert error_origin(repr(qdrant)) == ARM  # bare repr: no status, so doubtful


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(code=st.integers(400, 599), kind=st.sampled_from(["httpx", "requests", "qdrant"]))
def test_service_status_follows_the_gateway_status_rule(code, kind):
    # oracle: the gateway's status rule (TRANSIENT_STATUS or >= 500), applied to our services
    build = {"httpx": _httpx_status, "requests": _requests_status, "qdrant": _qdrant_status}
    want = TRANSPORT if code in TRANSIENT_STATUS or code >= 500 else ARM
    assert error_origin(error_text(build[kind](code))) == want


@pytest.mark.criterion("C8")
def test_the_runner_stores_a_qdrant_status_it_can_classify(kit):
    # oracle: requirement 13 — a system's exception is stored as error_text, so a Qdrant 503
    # raised through the runner is a transport error
    from src.eval.core.checkpoint.run import run_checkpointed
    from src.eval.core.runner import RunnerConfig

    class _Qdrant503:
        name = "fake_system"

        def answer(self, case):
            raise _qdrant_status(503)

    cases = [kit.make_case("q1")]
    (report,) = run_checkpointed(
        cases, [_Qdrant503()], store=kit.MemoryStore(),
        identities=[kit.identity_for(cases, "fake_system")], run_ids=["run-q"],
        config=RunnerConfig(max_workers=1), suite="spec_suite", suite_version="v1")
    (result,) = report.results
    assert result.grade.error == error_text(_qdrant_status(503))
    assert error_origin(result.grade.error) == TRANSPORT
    assert report.config["error_origins"] == {"transport": 1, "arm": 0}
