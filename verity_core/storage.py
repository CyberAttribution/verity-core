"""GCS authentication + raw upload (Sprint 1 Phase 2 step 3).

Dual credential modes, mirroring the two services' existing init logic:
  * service-account file present  -> PhishFinder mode (explicit credentials)
  * else bucket_name provided      -> Verity mode (Application Default Creds)
  * else                           -> disabled (best-effort no-op)

Callers keep their own blob-name construction (path prefixes, side-effects);
this module only owns auth + the upload transport.
"""
import os

from google.cloud import storage
from google.oauth2 import service_account


class Handle:
    """Opaque GCS handle. `.enabled` is True only when an uploadable bucket exists."""

    def __init__(self, client=None, bucket=None):
        self.client = client
        self.bucket = bucket

    @property
    def enabled(self):
        return self.bucket is not None


def get_handle(bucket_name=None, credentials_path="/etc/secrets/gcs_credentials.json"):
    """
    Resolve a GCS handle. Best-effort: any failure yields a disabled handle
    rather than raising, so importing a service never crashes on GCS config.
    """
    try:
        if os.path.exists(credentials_path):
            # PhishFinder mode: explicit service-account credentials file.
            credentials = service_account.Credentials.from_service_account_file(
                credentials_path
            )
            client = storage.Client(credentials=credentials)
            bucket = client.bucket(bucket_name) if bucket_name else None
            if bucket is not None:
                print("✅ Successfully initialized Google Cloud Storage client.")
            return Handle(client=client, bucket=bucket)
        elif bucket_name:
            # Verity mode: Application Default Credentials.
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            # Fail fast if the bucket doesn't exist or perms are wrong.
            _ = bucket.exists()
            print(f"GCS INIT: Logging enabled for bucket '{bucket_name}'")
            return Handle(client=client, bucket=bucket)
        else:
            print("GCS INIT: No credentials file and no bucket; GCS logging disabled.")
            return Handle()
    except Exception as e:
        print(f"WARNING: Could not initialize Google Cloud Storage or bucket. Error: {e}")
        return Handle()


def upload_string(handle, blob_name, content, content_type):
    """
    Best-effort upload of `content` to `blob_name`. Returns True on success,
    False if the handle is disabled. Raises only on a real transport failure
    (callers wrap this exactly as they did their inline transport before).
    """
    if not handle or not handle.enabled:
        return False
    blob = handle.bucket.blob(blob_name)
    blob.upload_from_string(content, content_type=content_type)
    return True
