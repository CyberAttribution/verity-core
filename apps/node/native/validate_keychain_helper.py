"""Synthetic native acceptance; prints status/reference metadata, never secrets."""
import argparse
import json
from pathlib import Path
import secrets
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from swarmtracker_node_ui.credentials import CredentialStore,CredentialUnavailable


def validate(helper):
    profile=str(uuid.uuid4());reference=str(uuid.uuid4());secret=secrets.token_urlsafe(32)
    store=CredentialStore(helper,profile)
    result=dict(synthetic_only=True,paid_calls=False,profile=profile,reference=reference,
                created=False,read_matches=False,wrong_origin_denied=False,
                wrong_profile_denied=False,delete_confirmed=False,missing_read_denied=False)
    try:
        store.create(provider='openai',endpoint='https://api.openai.com',secret=secret,reference=reference)
        result['created']=True
        result['read_matches']=store.read(reference,provider='openai',endpoint='https://api.openai.com')==secret
        try:store.read(reference,provider='openai',endpoint='https://other.example')
        except CredentialUnavailable as error:result['wrong_origin_denied']=str(error)=='Credential binding unavailable'
        other=CredentialStore(helper,str(uuid.uuid4()))
        try:other.read(reference,provider='openai',endpoint='https://api.openai.com')
        except CredentialUnavailable as error:result['wrong_profile_denied']=error.code=='not_found'
        store.delete(reference);result['delete_confirmed']=True
        try:store.read(reference,provider='openai',endpoint='https://api.openai.com')
        except CredentialUnavailable as error:result['missing_read_denied']=error.code=='not_found'
    except CredentialUnavailable as error:
        result['error_code']=error.code or 'operation_unconfirmed'
        result['outcome_unknown']=error.outcome_unknown
    finally:
        try:store.delete(reference);result['cleanup_confirmed']=True
        except CredentialUnavailable:result['cleanup_confirmed']=False
    required=('created','read_matches','wrong_origin_denied','wrong_profile_denied','delete_confirmed','missing_read_denied','cleanup_confirmed')
    result['native_validation_succeeded']=all(result.get(key) is True for key in required)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('helper');args=parser.parse_args()
    result=validate(Path(args.helper).resolve());print(json.dumps(result,sort_keys=True))
    return 0 if result['native_validation_succeeded'] else 1


if __name__=='__main__':raise SystemExit(main())
