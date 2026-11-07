"""Report declared memory-stage traffic and queueing separately from hardware claims."""
from .event_lifecycle import validate_events

def memory_summary(graph, nodes):
    transactions=graph.get("memory_transactions")
    if transactions is None: return None
    if not isinstance(transactions,list):raise ValueError('modeled memory transactions must be a list')
    if nodes!=validate_events(graph):raise ValueError('memory node map differs from graph events')
    groups={};seen=set();states={"completed":0,"inflight":0,"pending":0}
    for tx in transactions:
        if not isinstance(tx,dict):raise ValueError('modeled memory transaction must be an object')
        if (type(tx.get("hart")) is not int or not 0<=tx["hart"]<graph["harts"] or
            type(tx.get("address")) is not int or not 0<=tx["address"]<2**64 or
            tx.get("access_bytes") not in (1,2,4,8) or type(tx.get("access_bytes")) is not int or
            tx["address"]+tx["access_bytes"]>2**64 or tx.get("kind") not in ("load","store","lr","sc","amo") or
            not isinstance(tx.get("path"),str) or not tx["path"].strip() or
            not isinstance(tx.get("stages"),list) or not tx["stages"]):
            raise ValueError("invalid modeled memory transaction")
        last=None
        for stage in tx["stages"]:
            if not isinstance(stage,dict):raise ValueError('modeled memory stage must be an object')
            node_id=stage.get("node");state=stage.get("state")
            if (type(node_id) is not int or node_id<0 or node_id in seen or not isinstance(state,str) or state not in states or
                not isinstance(stage.get('name'),str) or not stage['name'].strip() or
                not isinstance(stage.get('resource'),str) or not stage['resource'].strip()):
                raise ValueError("invalid or duplicate modeled memory stage")
            seen.add(node_id);states[state]+=1
            for key in ("first_beat_latency","launch_interval","beat_interval","beat_bytes","bytes","derived_latency","derived_interval","issue_period"):
                if type(stage.get(key)) is not int or stage[key]<0:
                    raise ValueError("invalid modeled memory stage parameter")
            if (not stage["first_beat_latency"] or not stage["launch_interval"] or not stage["issue_period"] or
                not isinstance(stage.get("source"),str) or not stage["source"].strip() or
                not isinstance(stage.get("port_source"),str) or not stage["port_source"].strip()):
                raise ValueError("missing modeled memory timing or source")
            latency,interval=stage["first_beat_latency"],stage["launch_interval"]
            if stage["bytes"]:
                if not stage["beat_bytes"] or not stage["beat_interval"]:
                    raise ValueError("modeled memory packet has no beat contract")
                beats=(stage["bytes"]+stage["beat_bytes"]-1)//stage["beat_bytes"]
                latency+=(beats-1)*stage["beat_interval"]
                interval=max(interval,beats*stage["beat_interval"])
            elif stage["beat_bytes"] or stage["beat_interval"]:
                raise ValueError("fixed memory stage has transfer parameters")
            if (latency,interval)!=(stage["derived_latency"],stage["derived_interval"]):
                raise ValueError("modeled memory stage derived costs disagree")
            if stage.get("included_in_events") is False:
                if node_id in nodes:
                    raise ValueError("excluded memory stage reported as activity")
                if state=="pending":
                    if stage.get("scheduled_start") is not None or stage.get("scheduled_end") is not None:
                        raise ValueError("unissued memory stage has scheduled timestamps")
                elif (state!="inflight" or type(stage.get("scheduled_start")) is not int or stage.get("scheduled_start")!=graph["cycles"] or
                      type(stage.get("scheduled_end")) is not int or stage["scheduled_end"]<=graph["cycles"]):
                    raise ValueError("excluded inflight stage is not at the snapshot frontier")
                last=node_id;continue
            node=nodes.get(node_id)
            if state=='pending':raise ValueError('pending memory stage cannot be included as issued activity')
            if node is not None and node.get('cancelled',False):
                raise ValueError('accepted memory stages must survive architectural cancellation')
            if (stage.get("included_in_events") is not True or node is None or
                node.get("completed",True)!=(state=="completed") or node["hart"]!=tx["hart"] or
                node["resource"]!=stage["resource"] or node["name"]!=tx["path"]+"."+stage["name"] or
                node["bytes"]!=stage["bytes"] or not node["deps"] or
                (last is not None and node["deps"]!=[last])):
                raise ValueError("modeled memory stage disagrees with graph")
            period=stage["issue_period"]
            if node["start"]%period:
                raise ValueError("modeled memory stage violates receiving clock")
            expected_end=node["start"]+((latency+period-1)//period)*period
            if (type(stage.get("scheduled_start")) is not int or type(stage.get("scheduled_end")) is not int or
                    stage.get("scheduled_start")!=node["start"] or stage.get("scheduled_end")!=expected_end):
                raise ValueError("memory stage scheduled timestamps disagree")
            if (state=="completed" and node["end"]!=expected_end) or (state=="inflight" and not node["end"]<expected_end):
                raise ValueError("modeled memory stage duration disagrees")
            ready=node["submitted"]
            for dep in node["deps"]:
                parent=nodes.get(dep)
                if parent is None or not parent.get("completed",True):
                    raise ValueError("issued memory stage depends on unfinished operation")
                ready=max(ready,parent["end"])
            ready=((ready+period-1)//period)*period
            if ready>node["start"]: raise ValueError("memory stage issued before readiness")
            key=(tx["hart"],node.get("region") or "unmarked",tx["path"],stage["resource"])
            group=groups.setdefault(key,{"stages":0,"completed_stages":0,"inflight_stages":0,
                "completed_stage_bytes":0,"port_queue_wait_cycles_sum":0,"clock_alignment_cycles_sum":0,
                "sources":set(),"port_sources":set()})
            group["stages"]+=1;group[state+"_stages"]+=1
            if state=="completed":group["completed_stage_bytes"]+=stage["bytes"]
            group["port_queue_wait_cycles_sum"]+=node["start"]-ready
            raw_ready=max([node["submitted"]]+[nodes[dep]["end"] for dep in node["deps"]])
            group["clock_alignment_cycles_sum"]+=ready-raw_ready
            group["sources"].add(stage["source"]);group["port_sources"].add(stage["port_source"])
            last=node_id
    rows=[]
    for (hart,region,path,resource),group in sorted(groups.items()):
        rows.append(dict(group,hart=hart,region=region,path=path,resource=resource,
                         sources=sorted(group["sources"]),port_sources=sorted(group["port_sources"])))
    return {"scope":"declared scalar memory model stages; not measured hardware traffic",
            "cycle_unit":"base_clock_ticks","transaction_count":len(transactions),"stage_states":states,
            "rows":rows,"hardware_certified":False,"calibrated":False,
            "limitations":["Stage bytes are separate traffic levels and must not be added into application bytes.",
                "Queue waits exclude dependency readiness and clock alignment; sums are not additive CPU stall cycles.",
                "SC success, cache hits, vector expansion and architectural fault filtering are not bus observations."]}
