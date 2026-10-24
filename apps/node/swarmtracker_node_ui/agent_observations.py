"""Display-only setup observations; never permission or installation evidence."""
import time


def configured_agents(configuration, runs):
    from .overview import connection_rows
    connections={row["id"]:row for row in connection_rows(configuration)}
    views=[]
    for agent in configuration.all():
        if agent["kind"]!="agent":
            continue
        body=agent["body"]
        connection=configuration.latest("connection",body["connection_id"])
        observation=connections.get(body["connection_id"])
        state=observation["state"] if observation else "unknown"
        detail=observation["detail"] if observation else "Primary connection is not recorded"
        def metadata(run):
            if run is None:
                return None
            cfg=run["configuration"]
            try:
                selected=cfg.get("selected_connection_id","")
                current=configuration.effective(agent["id"],run["resource_id"],selected)
                matches=current==cfg
            except (ValueError,KeyError,TypeError):
                matches=False
            result=run.get("result") or {}
            return dict(id=run["id"],task_id=run["task_id"],at=run["at"],
                        state=run["state"],connection_id=cfg["connection_id"],
                        model=run["model"],configuration_matches=matches,
                        runtime_event_id=result.get("runtime_event_id"),
                        response_sha256=result.get("response_sha256"))
        view=dict(agent)
        view["setup_observation"]=dict(
            assessed_at=time.time(),connection_state=state,connection_detail=detail,
            connection_observed_at=observation["at"] if observation else None,
            adapter_supported=body.get("adapter_kind","openclaw_v0") in {"openclaw_v0","ollama_local_v1"}
                and bool(connection) and connection["body"]["scope"]=="local"
                and connection["body"]["provider"]==('ollama_local' if body.get('adapter_kind')=='ollama_local_v1' else 'openclaw'),
            latest_attempt=metadata(runs.latest_for_agent(agent["id"])),
            last_completed=metadata(runs.latest_for_agent(agent["id"],state="completed")))
        views.append(view)
    return views
