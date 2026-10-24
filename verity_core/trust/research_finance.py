"""Reproducible research arithmetic, not a valuation or investment forecast.

Reported observations remain claims. Currency/year normalization is never implicit.
Scenarios are hypothetical cash-flow illustrations, not audited producer returns.
"""
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, localcontext
import re
from urllib.parse import urlsplit

VERSION='research_finance/v0.1'
CATEGORIES=('production_budget','marketing_distribution','theatrical_gross','distributor_receipts','producer_receipts')


def number(value,*,nullable=False):
    if value is None and nullable:return None
    if not isinstance(value,str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,6})?',value):raise ValueError('Nonnegative bounded decimal string required; missing is null')
    try:v=Decimal(value)
    except InvalidOperation:raise ValueError('Invalid decimal') from None
    if not v.is_finite():raise ValueError('Finite decimal required')
    return v


def bounded(v,n):
    if not isinstance(v,str) or not v.strip() or len(v)>n:raise ValueError('Bounded text required')


def validate_observation(value):
    o=copy.deepcopy(value)
    if not isinstance(o,dict) or set(o)!={'id','subject_id','category','value','currency','territory','period','classification','source','dependence','uncertainty','transformations'}:raise ValueError('Exact financial observation fields required')
    for k in ('id','subject_id'):
        if not isinstance(o[k],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',o[k]):raise ValueError('Bounded observation/subject identity required')
    if o['category'] not in CATEGORIES or o['classification'] not in ('reported','estimated','independently_corroborated','missing'):raise ValueError('Supported category and observation classification required; assumptions separate')
    number(o['value'],nullable=True)
    if (o['value'] is None)!=(o['classification']=='missing'):raise ValueError('Missing value must be null and explicitly classified')
    if not isinstance(o['currency'],str) or not re.fullmatch('[A-Z]{3}',o['currency']):raise ValueError('Currency code required; no implicit conversion')
    if o['territory'] not in ('domestic','international','worldwide','not_applicable'):raise ValueError('Explicit territory scope required')
    bounded(o['period'],200);bounded(o['uncertainty'],1000)
    if not isinstance(o['transformations'],list) or len(o['transformations'])>10:raise ValueError('Bounded transformations required')
    for t in o['transformations']:bounded(t,1000)
    s=o['source']
    if not isinstance(s,dict) or set(s)!={'url','publisher','published_at','retrieved_at','passage','collection'}:raise ValueError('Source provenance required')
    u=urlsplit(s['url']);bounded(s['url'],1000)
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment or u.port not in (None,443):raise ValueError('Public HTTPS source reference required')
    bounded(s['publisher'],200);bounded(s['passage'],2000)
    if s['published_at'] is not None:dt.date.fromisoformat(s['published_at'])
    stamp=dt.datetime.fromisoformat(s['retrieved_at'].replace('Z','+00:00'))
    if stamp.tzinfo is None or stamp.timestamp()>dt.datetime.now(dt.timezone.utc).timestamp()+60:raise ValueError('Actual retrieval timestamp with timezone required')
    if s['collection'] not in ('operator_public_excerpt','permitted_live_retrieval','synthetic_fixture'):raise ValueError('Collection provenance required')
    d=o['dependence']
    if not isinstance(d,dict) or set(d)!={'status','origin','basis'} or d['status'] not in ('unknown','asserted','inferred','observed'):raise ValueError('Source-dependence assertion required')
    bounded(d['origin'],1000);bounded(d['basis'],1000)
    if o['classification']=='independently_corroborated':raise ValueError('Pilot cannot establish independent corroboration from a supplied label; retain separate reported observations and review relationships')
    return o


def effective_observations(entries):
    observations={}
    for e in entries:
        p=e['payload']
        if p['kind']=='observation':observations[p['body']['id']]=validate_observation(p['body'])
        elif p['kind']=='correction':observations[p['body']['observation_id']]=validate_observation(p['body']['replacement'])
    return list(observations.values())


def comparisons(observations,subjects):
    """List claims separately; never select conflicting values or add territory totals."""
    rows=[]
    for subject in subjects:
        obs=[o for o in observations if o['subject_id']==subject['id']]
        budgets=[o for o in obs if o['category']=='production_budget' and o['value'] is not None]
        grosses=[o for o in obs if o['category']=='theatrical_gross' and o['value'] is not None]
        ratios=[]
        for b in budgets:
            for g in grosses:
                if b['currency']==g['currency'] and number(b['value'])>0:
                    with localcontext() as ctx:
                        ctx.prec=40;ratio=format(number(g['value'])/number(b['value']),'.6f')
                    ratios.append(dict(budget_id=b['id'],gross_id=g['id'],territory=g['territory'],budget_scope=b['territory'],scope_mismatch=b['territory']!=g['territory'],gross_to_reported_budget=ratio,meaning='Gross/budget comparison only; not profitability. Original reporting years unadjusted.'))
        rows.append(dict(subject=subject,observations=obs,comparisons=ratios,missing_categories=[c for c in CATEGORIES if not any(o['category']==c and o['value'] is not None for o in obs)]))
    return rows


def scenarios(assumptions):
    if not isinstance(assumptions,list) or not 1<=len(assumptions)<=36:raise ValueError('One to36 explicit scenarios required')
    rows=[];names=set()
    for a in assumptions:
        if set(a)!={'name','production_budget','theatrical_gross','exhibitor_share','distribution_fee','marketing_distribution','ancillary_producer_receipts','currency','basis'}:raise ValueError('Exact scenario assumptions required')
        bounded(a['name'],100);bounded(a['basis'],2000)
        if a['name'] in names:raise ValueError('Duplicate scenario name')
        names.add(a['name'])
        if not re.fullmatch('[A-Z]{3}',a['currency']):raise ValueError('Scenario currency required')
        vals={k:number(a[k],nullable=True) for k in ('production_budget','theatrical_gross','exhibitor_share','distribution_fee','marketing_distribution','ancillary_producer_receipts')}
        for k in ('exhibitor_share','distribution_fee'):
            if vals[k] is not None and vals[k]>1:raise ValueError('Share/fee must be between0 and1')
        missing=[k for k,v in vals.items() if v is None]
        result=None
        if not missing:
            with localcontext() as ctx:
                ctx.prec=40
                rentals=vals['theatrical_gross']*(1-vals['exhibitor_share'])
                receipts=rentals*(1-vals['distribution_fee'])+vals['ancillary_producer_receipts']
                result=dict(modeled_distributor_rentals=format(rentals,'.2f'),modeled_cash_after_listed_costs=format(receipts-vals['production_budget']-vals['marketing_distribution'],'.2f'))
        rows.append(dict(assumptions=copy.deepcopy(a),result=result,missing=missing,formula='rentals=gross*(1-exhibitor_share); cash=rentals*(1-distribution_fee)+ancillary_producer_receipts-production_budget-marketing_distribution',limitations='Illustrative simplified waterfall; financing, residuals, taxes, timing, overhead and unlisted expenses excluded. Not investment returns or a forecast.'))
    return dict(version=VERSION,scenarios=rows,sensitivity='Compare explicit low/base/high assumption rows; all differences are assumptions, not collected financial evidence.')


def disagreements(observations):
    """Conflicting recorded amounts in identical scopes; no truth selection.

    Different textual periods are not normalized. Publisher agreement is not
    evidence of independent origins, and this check does not assess materiality.
    Historical calculation output remains unchanged by this read-only view.
    """
    groups={}
    for o in observations:
        validate_observation(o)
        if o['value'] is None:continue
        key=tuple(o[k] for k in ('subject_id','category','currency','territory','period'))
        groups.setdefault(key,[]).append(o)
    result=[]
    for key,claims in groups.items():
        if len({number(o['value']) for o in claims})<=1:continue
        result.append(dict(scope=dict(zip(('subject_id','category','currency','territory','period'),key)),claims=[dict(id=o['id'],value=o['value'],publisher=o['source']['publisher'],url=o['source']['url'],dependence=o['dependence']) for o in claims],status='Unresolved disagreement; all reported values retained. No averaging, truth selection, independence or financial materiality established.'))
    return result
