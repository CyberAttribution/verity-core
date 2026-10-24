"""Shared explicit-request report journey, independently of agent framework.

The framework/session carries no approval. Only the existing report broker
executes the fixed action after an explicit local decision. Recovery requires
the exact retained request ID and never selects an older same-task candidate.
"""
import hashlib
import uuid
from .customer_config import canonical
from . import report_actions


class ReportJourney:
    def __init__(self,inference,actions):
        self.inference=inference;self.actions=actions

    def propose(self,*,agent_id,task_id,request_id,session_id,prompt,configuration,source_name, recover=False):
        if str(uuid.UUID(request_id))!=request_id:raise ValueError('Exact existing request UUID required')
        if configuration['agent_id']!=agent_id or configuration['resource_id']!=report_actions.TARGET:
            raise ValueError('Reviewed agent/resource differs')
        config_sha=hashlib.sha256(canonical(configuration).encode()).hexdigest()
        if recover:
            run=self.inference.get(request_id)
            if (run['id']!=request_id or run['task_id']!=task_id or run['agent_id']!=agent_id
                    or run['session_id']!=session_id or run['resource_id']!=report_actions.TARGET
                    or run['purpose']!='draft_text' or run['prompt_sha256']!=hashlib.sha256(prompt.encode()).hexdigest()
                    or run['configuration']!=configuration):
                raise ValueError('Recovery identity or reviewed content differs')
        else:
            run=self.inference.run(agent_id=agent_id,task_id=task_id,resource_id=report_actions.TARGET,purpose='draft_text',
                    prompt=prompt,session_id=session_id,request_id=request_id,expected_configuration_sha256=config_sha,
                    requested_connection_id=configuration.get('selected_connection_id','') if configuration.get('selection_reason')=='operator_explicit' else '')
        if run['state']=='denied':return dict(state='denied',request_id=run['id'],reason=run['reason'])
        if run['state']!='completed':raise ValueError('Request unfinished or uncertain; no automatic redispatch')
        result=run['result'];body=result['response']
        manifest=dict(schema=report_actions.SCHEMA,agent_id=agent_id,task_id=task_id,model=run['model'],
                provider=run['configuration']['provider'],source_name=source_name,source_sha256=run['prompt_sha256'],
                runtime_event_id=result['runtime_event_id'],activity_run_id=run['id'],response_sha256=result['response_sha256'],
                proposal=body,action=dict(kind='write_sanitized_summary',target=report_actions.TARGET,sha256=report_actions.digest(body.encode())),
                policy=report_actions.POLICY,effective_configuration=run['configuration'])
        proposal=self.actions.create_bound(manifest,self.inference)
        return dict(state=proposal['state'],request_id=run['id'],proposal_id=proposal['id'],proposal_head=proposal['head_hash'],
                    scope='Verity-mediated request and fixed broker action; framework and OS identity unverified; no containment claim')


def adk_agent(journey,call):
    """Optional real ADK dependency; never imported in installed customer runtime."""
    from google.adk.agents import BaseAgent
    from google.adk.events import Event
    from google.genai import types
    from pydantic import PrivateAttr
    import asyncio,json
    class VerityReportAgent(BaseAgent):
        _journey:object=PrivateAttr()
        _call:dict=PrivateAttr()
        def __init__(self):
            super().__init__(name='verity_report_conformance',description='Disposable Verity-mediated report proposal')
            self._journey=journey;self._call=dict(call)
        async def _run_async_impl(self,ctx):
            result=await asyncio.to_thread(self._journey.propose,**self._call)
            yield Event(author=self.name,content=types.Content(role='model',parts=[types.Part(text=json.dumps(result))]))
    return VerityReportAgent()
