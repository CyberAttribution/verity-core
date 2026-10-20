"""macOS credential storage boundary; not a paid-inference authorization.

The packager supplies the trusted native helper executable. Secrets are not
written into configuration, argv, environment, or error messages. This does not
isolate malicious code running as the same OS user or erase Python memory.
"""
import json
import subprocess
import uuid
from urllib.parse import urlsplit


class CredentialUnavailable(ValueError):
    pass


def identity(value):
    if not isinstance(value,str):raise ValueError('UUID required')
    try:parsed=uuid.UUID(value)
    except (ValueError,AttributeError):raise ValueError('UUID required') from None
    if str(parsed)!=value:raise ValueError('Canonical UUID required')
    return value


def binding(provider,endpoint):
    if not isinstance(provider,str) or provider not in {'openai','anthropic','gemini'}:
        raise ValueError('Supported credential provider required')
    if not isinstance(endpoint,str):raise ValueError('HTTPS origin required')
    try:
        u=urlsplit(endpoint)
        valid=u.scheme=='https' and u.hostname and not u.username and not u.password and not u.query and not u.fragment and u.path in ('','/') and u.port in (None,443)
    except ValueError:valid=False
    if not valid:raise ValueError('HTTPS origin required')
    return dict(provider=provider,endpoint='https://'+u.hostname.lower())


class CredentialStore:
    def __init__(self,helper,profile,*,runner=subprocess.run):
        self.helper=str(helper);self.profile=identity(profile);self.runner=runner

    def _call(self,operation,reference,**fields):
        request=dict(operation=operation,profile=self.profile,reference=identity(reference),**fields)
        try:
            result=self.runner([self.helper],input=json.dumps(request).encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,check=False)
            if result.returncode!=0 or len(result.stdout)>65536:raise CredentialUnavailable('Credential storage unavailable')
            response=json.loads(result.stdout)
            if not isinstance(response,dict) or response.get('ok') is not True:raise CredentialUnavailable('Credential storage unavailable')
            return response
        except (OSError,subprocess.SubprocessError,ValueError,TypeError):
            raise CredentialUnavailable('Credential storage unavailable') from None

    def create(self,*,provider,endpoint,secret):
        bound=binding(provider,endpoint)
        if not isinstance(secret,str) or not 1<=len(secret.encode())<=8192 or any(ord(c)<33 or ord(c)>126 for c in secret):
            raise ValueError('Bounded nonempty ASCII API credential required')
        reference=str(uuid.uuid4())
        self._call('create',reference,value=json.dumps(dict(bound,secret=secret),sort_keys=True))
        return reference

    def read(self,reference,*,provider,endpoint):
        expected=binding(provider,endpoint)
        response=self._call('read',reference)
        try:
            value=json.loads(response['value'])
            if set(value)!={'provider','endpoint','secret'} or any(value[k]!=v for k,v in expected.items()):raise ValueError()
            secret=value['secret']
            if not isinstance(secret,str) or not 1<=len(secret.encode())<=8192 or any(ord(c)<33 or ord(c)>126 for c in secret):raise ValueError()
            return secret
        except (KeyError,ValueError,TypeError):raise CredentialUnavailable('Credential binding unavailable') from None

    def delete(self,reference):
        self._call('delete',reference)
