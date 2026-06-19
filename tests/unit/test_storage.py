"""Unit tests for verity_core.storage (GCS client mocked, offline)."""
from verity_core import storage as st


class _FakeBlob:
    def __init__(self, record):
        self._record = record

    def upload_from_string(self, content, content_type):
        self._record.append((content, content_type))


class _FakeBucket:
    def __init__(self, record):
        self._record = record

    def blob(self, name):
        self._record.append(("blob", name))
        return _FakeBlob(self._record)

    def exists(self):
        return True


class _FakeClient:
    def __init__(self, record):
        self._record = record

    def bucket(self, name):
        return _FakeBucket(self._record)


def test_service_account_mode_when_creds_file_exists(monkeypatch):
    monkeypatch.setattr(st.os.path, "exists", lambda p: True)
    monkeypatch.setattr(
        st.service_account.Credentials, "from_service_account_file",
        staticmethod(lambda p: object()),
    )
    monkeypatch.setattr(st.storage, "Client", lambda credentials=None: _FakeClient([]))
    h = st.get_handle(bucket_name="b")
    assert h.enabled is True


def test_adc_mode_when_only_bucket_set(monkeypatch):
    monkeypatch.setattr(st.os.path, "exists", lambda p: False)
    monkeypatch.setattr(st.storage, "Client", lambda: _FakeClient([]))
    h = st.get_handle(bucket_name="b")
    assert h.enabled is True


def test_disabled_when_no_creds_and_no_bucket(monkeypatch):
    monkeypatch.setattr(st.os.path, "exists", lambda p: False)
    h = st.get_handle()
    assert h.enabled is False
    # upload_string no-ops and returns False when disabled.
    assert st.upload_string(h, "x/y.json", "{}", "application/json") is False


def test_upload_string_passthrough_when_enabled(monkeypatch):
    record = []
    monkeypatch.setattr(st.os.path, "exists", lambda p: False)
    monkeypatch.setattr(st.storage, "Client", lambda: _FakeClient(record))
    h = st.get_handle(bucket_name="b")
    assert st.upload_string(h, "prefix/name.json", "payload", "application/json") is True
    assert ("blob", "prefix/name.json") in record
    assert ("payload", "application/json") in record
